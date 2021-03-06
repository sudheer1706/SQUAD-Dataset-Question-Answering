import torch
import torch.optim as optim
import torch.nn.functional as F
import torch.nn as nn
from torch.autograd import Variable

import numpy as np
from torch.nn.utils.rnn import pad_packed_sequence
from torch.nn.utils.rnn import pack_padded_sequence


def cuda(obj):
    if torch.cuda.is_available():
        obj = obj.cuda()

    return obj

class Encoder(nn.Module):
    
    def __init__(self, embedding_size, hidden_size, vocab_size, num_layers=1,dropout_p=0.3):
        super(Encoder, self).__init__()
        
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.embedding = nn.Embedding (vocab_size, embedding_size,  padding_idx=0) 
        self.lstm1 = nn.LSTM (embedding_size, hidden_size, num_layers, batch_first=True)
        self.lstm2 = nn.LSTM (hidden_size*3, hidden_size, batch_first=True, bidirectional=True)
        self.linear = nn.Linear (hidden_size, hidden_size)
        self.dropout = nn.Dropout (dropout_p) 
        
        
    def init_embedding(self,pretrained_wvectors):
        self.embedding.weight = nn.Parameter (torch.from_numpy (pretrained_wvectors).float())
        self.embedding.weight.requires_grad = False    
  
    def init_state(self,size):
        hidden = Variable (torch.zeros (self.num_layers, size, self.hidden_size))
        context = Variable (torch.zeros (self.num_layers, size, self.hidden_size))
        return (cuda(hidden), cuda(context))
      
    def init_state_bdir(self,size):
        hidden = Variable (torch.zeros (2*self.num_layers, size, self.hidden_size))
        context = Variable (torch.zeros (2*self.num_layers, size, self.hidden_size))
        return (cuda(hidden), cuda(context))
      
    
    def forward(self, documents, questions, doc_lens, question_lens, is_training=False):
        documents = self.embedding(documents)
        if is_training:
            documents = self.dropout (documents)
        hidden = self.init_state (documents.size(0))
        q_sorted, indices = torch.sort (doc_lens, 0, True)
        q_sorted = [i for i in q_sorted]
        docs = pack_padded_sequence (documents[indices], q_sorted, batch_first=True)
        output, hidden = self.lstm1 (docs, hidden)
        output = pad_packed_sequence (output, batch_first=True)[0]
        sorted_idx, idx = torch.sort(indices, 0)
        output = output[idx]
        sentinel = Variable (torch.zeros (documents.size(0), 1,self.hidden_size))
        sentinel = cuda (sentinel)
        D = torch.cat ([output,sentinel], 1) 
        
        questions = self.embedding(questions)
        if is_training:
            questions = self.dropout (questions)
        hidden = self.init_state (questions.size(0))
        q_sorted, indices = torch.sort (question_lens, 0, True)
        ques = pack_padded_sequence (questions[indices], q_sorted.tolist(), batch_first=True)
        output, hidden = self.lstm1 (ques, hidden)
        output = pad_packed_sequence (output, batch_first=True)[0]
        sorted_idx, idx = torch.sort (indices, 0)
        output = output[idx]
        sentinel = Variable (torch.zeros (questions.size(0), 1, self.hidden_size))
        sentinel = cuda (sentinel)
        Q_prime = torch.cat ([output,sentinel], 1)
        
        linear = self.linear (Q_prime.view (Q_prime.size(0) * Q_prime.size(1), -1))
        Q = F.tanh (linear)
        Q = Q.view (Q_prime.size(0), Q_prime.size(1), -1) 
        L = torch.bmm (D,Q.transpose(1, 2)) 
        Doc_Attn = []
        Ques_Attn = []
        for i in range(L.size(0)):
            Ques_Attn.append (F.softmax(L[i],1).unsqueeze(0)) 
            Doc_Attn.append (F.softmax(L[i].transpose(0, 1), 1).unsqueeze(0)) 
        AD = torch.cat (Doc_Attn) 
        AQ = torch.cat (Ques_Attn) 
        Ques_context = torch.bmm (D.transpose(1,2), AQ).transpose(1, 2) 
        Ques_context = torch.cat ([Q, Ques_context], 2)
        Ques_context = Ques_context.transpose(1, 2)
        CD = torch.bmm (Ques_context, AD).transpose(1, 2) 
        h_size = CD.size(0)
        hidden = self.init_state_bdir(h_size)
        ip = torch.cat([D,CD], 2)
        op, h = self.lstm2 (ip, hidden)
        
        return  op
    
    
class Max(nn.Module):
    def __init__(self, input_size, output_size, pool_size):
        super().__init__()
        self.input_size = input_size
        self.output_size = output_size
        self.pool_size = pool_size
        self.linear = nn.Linear (input_size, output_size * pool_size)

    def forward(self, inputs):
        shape = list(inputs.size())
        shape[-1] = self.output_size
        shape.append(self.pool_size)
        max_dim = len(shape) - 1
        out = self.linear(inputs)
        m, i = out.view(*shape).max(len(shape) - 1)
        return m
    
    
class HighwayMax(nn.Module):
    def __init__(self, hidden_size,pool_size=8):
        super(HighwayMax, self).__init__()
        self.hidden_size = hidden_size
        self.pool_size = pool_size
        self.WD = nn.Linear (5*hidden_size,hidden_size)
        self.W1 = Max (hidden_size*3,hidden_size,pool_size)
        self.W2 = Max (hidden_size,hidden_size,pool_size)
        self.W3 = Max (hidden_size*2,1,pool_size)
        
    def forward(self,ut,us,ue,hidden):
        r = F.tanh( self.WD( torch.cat([us,ue,hidden],1))) 
        m1 = self.W1 (torch.cat([ut,r],1))
        m2 = self.W2 (m1)
        m3 = self.W3 (torch.cat([m1,m2],1))
        
        return m3
    
    
class Decoder(nn.Module):
    def __init__(self,hidden_size,pooling_size=8,dropout_p=0.3,max_iter=4):
        super(Decoder,self).__init__()
        
        self.hidden_size = hidden_size
        self.max_iter = max_iter
        self.lstm = nn.LSTMCell (hidden_size*4,hidden_size)
        self.start = HighwayMax (hidden_size,pooling_size)
        self.end = HighwayMax (hidden_size,pooling_size)
        self.dropout = nn.Dropout (dropout_p)

        
    def init_state(self,size):
        hidden = Variable (torch.zeros (size,self.hidden_size))
        context = Variable (torch.zeros (size,self.hidden_size))
        return (cuda (hidden),cuda (context))
        
    def forward(self, enc_op, is_training=False):
       
        s = 0
        e = 1
        entropies=[]
        enc_op = enc_op[:, :-1]
        batch_size = enc_op.size(0)
        
        us = torch.cat ([i[s].unsqueeze(0) for i in enc_op]) 
        ue = torch.cat ([i[e].unsqueeze(0) for i in enc_op]) 
        
        hidden = self.init_state (batch_size)
       
        for i in range(self.max_iter):
            E=[]
            A=[]
            for ut in enc_op.transpose(0,1): 
                start = self.start (ut, us, ue, hidden[0])
                A.append(start)
                
            alpha = torch.cat (A,1)
            E.append (alpha)
            alpha = alpha.max (1)[1] 
            
            #temp = []
            #for i in range(batch_size):
            #    e_o = enc_op[i][alpha.data[i]]
            #    e_o = e_o.unsqeeze(0)
            #    temp.append(e_o)
                
            #us = torch.cat(temp)
            us = torch.cat([enc_op[i][alpha.data[i]].unsqueeze(0) for i in range(batch_size)]) 
            
            B=[]
            for ut in enc_op.transpose (0,1):
                end = self.end (us, ut, ue, hidden[0])
                B.append (end)
                
            beta = torch.cat (B,1)
            E.append (beta)
            beta = beta.max(1)[1] 
            
            
            #temp = []
            #for i in range(batch_size):
            #    e_o = enc_op[i][beta.data[i]]
            #    e_o = e_o.unsqueeze(0)
            #    temp.append(e_o)
                
            #ue = torch.cat(temp)
            ue = torch.cat([enc_op[i][beta.data[i]].unsqueeze(0) for i in range(batch_size)]) 
            entropies.append (E)
                        
            if is_training == False and alpha.data[0] == s and beta.data[0] == e:
                break
            else:
                s = alpha.data[0]
                e = beta.data[0]
            
            ip = torch.cat([us,ue],1)
            hidden = self.lstm (ip, hidden) 
            
        s_ent, e_ent = list(zip(*entropies))
        return alpha, beta, s_ent, e_ent