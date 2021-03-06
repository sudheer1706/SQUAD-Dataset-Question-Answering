import random
import torch
import torch.nn as nn
from torch.autograd import Variable
import torch.optim as optim
import torch.nn.functional as F
import numpy as np
import gensim
import pickle


from data import LoadSquadDataset,preprop,prepareBatch,padSentences, prepareSentence
#from data import preprop
from model import Encoder, Decoder

def cuda(obj):
    if torch.cuda.is_available():
        obj = obj.cuda()

    return obj


def main():
  
    embedding_size = 300
    hidden_size = 200
    
    maxout = 4
    max_iter = 4
    batch_size = 64 
    max_len = 600
    lr = 0.0001
    num_epochs = 50
    
    dataset_train = LoadSquadDataset('train-v1.1.json', max_len)
    token2id = preprop(dataset_train)
    train_data = prepareSentence(dataset_train,token2id)
    print('len train_data', len(train_data))
    
    vocab_size = len(token2id)
    model = gensim.models.KeyedVectors.load_word2vec_format('glove.840B.300d.w2vformat.txt')
    glove_embed = []
    oov_idx=[]

    #Fetching GloVe Embeddings
    for i, key in enumerate(token2id.keys()):
        try:
            glove_embed.append(model[key])
        except:
            glove_embed.append(np.zeros(300))
            oov_idx.append(i)
        
        
    g_embed_vec = np.vstack(glove_embed)
    print(len(oov_idx),"/",vocab_size)

    
    encoder = Encoder(embedding_size, 
                      hidden_size, 
                      vocab_size)
    
    decoder = Decoder(hidden_size, 
                      maxout, 
                      max_iter=max_iter)
    
    e_optimizer = optim.Adam(filter(lambda p: p.requires_grad, encoder.parameters()),lr=lr)
    d_optimizer = optim.Adam(decoder.parameters(), lr=lr*5)
    
    criterion = nn.CrossEntropyLoss()
    
    encoder.init_embedding(g_embed_vec)
    
    encoder = cuda(encoder)
    decoder = cuda(decoder)
    
    # training
	for epoch in range(num_epochs):
        losses=[]
        for i,batch in enumerate(prepareBatch(train_data, batch_size)):
            documents,questions,doc_lens,question_lens,s_target,e_target = padSentences(batch,token2id)

            encoder.zero_grad()
            decoder.zero_grad()
           
            output = encoder(documents,questions,doc_lens,question_lens,True)
            s, e, s_output, e_output = decoder(output,True)
            #print (s,e,s_output,e_output)
        
            loss_start,loss_end=0,0
            for m in range(len(s_output)):
                loss_start += criterion(s_output[m],s_target.view(-1))
                loss_end += criterion(e_output[m],e_target.view(-1))

            loss = loss_start + loss_end          
            losses.append(loss.data[0])
            loss.backward()
 
            e_optimizer.step()
            d_optimizer.step()
            if i == 3:
              break;

    
	# sample testing
    id2token={v:k for k,v in token2id.items()}

    dataset_test = LoadSquadDataset('dev-v1.1.json')
    token2id = preprop(dataset_test,token2id)
    dataset_test = prepareSentence(dataset_test,token2id)
    test = random.choice(train_data)
    
    doc_len = torch.cuda.LongTensor([test[0].size(1)])
    ques_len = torch.cuda.LongTensor([test[1].size(1)])

    U = encoder(test[0],test[1],doc_len,ques_len)
    s, e, s_op, e_op = decoder(U)
    
    paragraph=[id2token[p] for p in test[0].data.cpu().tolist()[0]]
    question =[id2token[p] for p in test[1].data.cpu().tolist()[0]]

    print(u'> {}'.format(paragraph))
    print(u'> {}'.format(question))
    if s.data[0]<=e.data[0]:
        print(u'= {}'.format(paragraph[s.data[0]:e.data[0]+1]))
    else:
        print(u'= {}'.format(paragraph[e.data[0]:s.data[0]+1]))
    print(u'< {}'.format(paragraph[test[2].data.tolist()[0][0]:test[3].data.tolist()[0][0]+1]))
    

	#testing and evaluation
    overlap=0
    predicted=0
    truth=0
    
    count = 0

    for test in dataset_test:
        U = encoder(test[0],test[1],torch.cuda.LongTensor([test[0].size(1)]),torch.cuda.LongTensor([test[1].size(1)]))
        s, e, s_op, e_op = decoder(U)
        paragraph=[id2token[p] for p in test[0].data.cpu().tolist()[0]]
        question =[id2token[p] for p in test[1].data.cpu().tolist()[0]]
    
        predict = list(range(s.data[0],e.data[0]+1))
        truths = list(range(test[2].squeeze(0).data[0],test[3].squeeze(0).data[0]+1))
        overlap+=len(set(truths) & set(predict))
        predicted+=len(predict)
        truth+=len(truths)
        if count == 10:
            break
        

    precision = overlap/predicted
    recall = overlap/truth

    f1_score = 2*precision*recall/(precision+recall)
    print(f1_score)

    
if __name__ == '__main__':
    main()
  