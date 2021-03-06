#!/usr/bin/env python
# coding: utf-8

# ### Requirements

# In[ ]:


from __future__ import unicode_literals, print_function, division

# from IPython import get_ipython
#
# get_ipython().run_line_magic('matplotlib', 'inline')
import unicodedata
import re
import random

import torch
import torch.nn as nn
from torch import optim
import torch.nn.functional as F

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# Each word in a language will be represented as a one-hot vector, or giant vector of zeros except for a single one (
# at the index of the word).
# 
# To keep track of all this we are using a helper class called Lang which has word → index (word2index) and index → word (index2word) dictionaries, as well as a count of each word word2count which will be used to replace rare words later.

# In[ ]:


SOS_token = 0
EOS_token = 1


class Lang:
    def __init__(self, name):
        self.name = name
        self.word2index = {}
        self.word2count = {}
        self.index2word = {0: "SOS", 1: "EOS"}
        self.n_words = 2  # Count SOS and EOS

    def addSentence(self, sentence):
        for word in sentence.split(' '):
            self.addWord(word)

    def addWord(self, word):
        if word not in self.word2index:
            self.word2index[word] = self.n_words
            self.word2count[word] = 1
            self.index2word[self.n_words] = word
            self.n_words += 1
        else:
            self.word2count[word] += 1


# To make sure the files are all in ASCII, make everything lowercase, and trim most punctuation.

# In[ ]:


# Turn a Unicode string to plain

def unicodeToAscii(s):
    return ''.join(
        c for c in unicodedata.normalize('NFD', s)
        if unicodedata.category(c) != 'Mn'
    )


# Lowercase, trim, and remove non-letter characters


def normalizeString(s):
    s = unicodeToAscii(s.lower().strip())
    s = re.sub(r"([.!?])", r" \1", s)
    s = re.sub(r"[^a-zA-Z.!?]+", r" ", s)
    return s


# To read the data file we will split the file into lines, and then split lines into pairs. The file is English → Chichewa, so if we want to translate from Chichewa → English we need to add the reverse flag to reverse the pairs.

# In[ ]:


# Read the data into a DataFrame
import pandas as pd
import os
import numpy as np


def readLangs(lang1, lang2, reverse=False):
    data_dir = os.getcwd() + '\\translator_data\\data'

    df = pd.read_csv(data_dir + '\\%s-%s.csv' % (lang1, lang2), names=[lang1, lang2], encoding='mac_roman')
    eng_lang = np.array(df[lang1])
    chi_lang = np.array(df[lang2])

    lines = []

    for i in range(len(eng_lang)):
        lines.append(eng_lang[i] + '\t' + chi_lang[i])

    # Read the file and split into lines
    # lines = open('data/%s-%s.csv' % (lang1, lang2), encoding='mac_roman').read().strip().split('\n')

    # Split every line into pairs and normalize
    pairs = [[normalizeString(s) for s in l.split('\t')] for l in lines]

    # Reverse pairs, make Lang instances
    if reverse:
        pairs = [list(reversed(p)) for p in pairs]
        input_lang = Lang(lang2)
        output_lang = Lang(lang1)
    else:
        input_lang = Lang(lang1)
        output_lang = Lang(lang2)

    return input_lang, output_lang, pairs


# The full process for preparing the data is:
# 
# - Read text file and split into lines, split lines into pairs
# - Normalize text, filter by length and content
# - Make word lists from sentences in pairs

# In[ ]:


def prepareData(lang1, lang2, reverse=False):
    input_lang, output_lang, pairs = readLangs(lang1, lang2, reverse)
    print("Read %s sentence pairs" % len(pairs))
    # pairs = filterPairs(pairs)
    # print("Trimmed to %s sentence pairs" % len(pairs))
    print("Counting words...")
    for pair in pairs:
        input_lang.addSentence(pair[0])
        output_lang.addSentence(pair[1])
    print("Counted words:")
    print(input_lang.name, input_lang.n_words)
    print(output_lang.name, output_lang.n_words)
    return input_lang, output_lang, pairs


input_lang, output_lang, pairs = prepareData('chi', 'eng', True)


# print(random.choice(pairs))


# ## The Seq2Seq Model
# 
# A Recurrent Neural Network, or RNN, is a network that operates on a sequence and uses its own output as input for subsequent steps.
# 
# A Sequence to Sequence network, or seq2seq network, or Encoder Decoder network, is a model consisting of two RNNs called the encoder and decoder. 
# The encoder reads an input sequence and outputs a single vector, and the decoder reads that vector to produce an output sequence.
# 
# Unlike sequence prediction with a single RNN, where every input corresponds to an output, the seq2seq model frees us 
# from sequence length and order, which makes it ideal for translation between two languages.

# ### The Encoder
# The encoder of a seq2seq network is a RNN that outputs some value for every word from the input sentence. For every 
# input word the encoder outputs a vector and a hidden state, and uses the hidden state for the next input word.

# In[ ]:


class EncoderRNN(nn.Module):
    def __init__(self, input_size, hidden_size):
        super(EncoderRNN, self).__init__()
        self.hidden_size = hidden_size

        self.embedding = nn.Embedding(input_size, hidden_size)
        self.gru = nn.GRU(hidden_size, hidden_size)

    def forward(self, input, hidden):
        embedded = self.embedding(input).view(1, 1, -1)
        output = embedded
        output, hidden = self.gru(output, hidden)
        return output, hidden

    def initHidden(self):
        return torch.zeros(1, 1, self.hidden_size, device=device)


# ### The Decoder
# The decoder is another RNN that takes the encoder output vector(s) and outputs a sequence of words to create the translation.
# 
# Simple Decoder
# In the simplest seq2seq decoder we use only last output of the encoder. This last output is sometimes called the context vector as it encodes context from the entire sequence. This context vector is used as the initial hidden state of the decoder.
# 
# At every step of decoding, the decoder is given an input token and hidden state. The initial input token is the start-of-string \<SOS\> token, and the first hidden state is the context vector (the encoder’s last hidden state).

# In[ ]:


class DecoderRNN(nn.Module):
    def __init__(self, hidden_size, output_size):
        super(DecoderRNN, self).__init__()
        self.hidden_size = hidden_size

        self.embedding = nn.Embedding(output_size, hidden_size)
        self.gru = nn.GRU(hidden_size, hidden_size)
        self.out = nn.Linear(hidden_size, output_size)
        self.softmax = nn.LogSoftmax(dim=1)

    def forward(self, input, hidden):
        output = self.embedding(input).view(1, 1, -1)
        output = F.relu(output)
        output, hidden = self.gru(output, hidden)
        output = self.softmax(self.out(output[0]))
        return output, hidden

    def initHidden(self):
        return torch.zeros(1, 1, self.hidden_size, device=device)


# #### The Attention Decoder
# If only the context vector is passed between the encoder and decoder, that single vector carries the burden of encoding the entire sentence.
# 
# Attention allows the decoder network to “focus” on a different part of the encoder’s outputs for every step of the decoder’s own outputs. First we calculate a set of attention weights. These will be multiplied by the encoder output vectors to create a weighted combination. The result (called attn_applied in the code) should contain information about that specific part of the input sequence, and thus help the decoder choose the right output words.
# 
# Calculating the attention weights is done with another feed-forward layer attn, using the decoder’s input and hidden state as inputs. Because there are sentences of all sizes in the training data, to actually create and train this layer we have to choose a maximum sentence length (input length, for encoder outputs) that it can apply to. Sentences of the maximum length will use all the attention weights, while shorter sentences will only use the first few.

# In[ ]:


MAX_LENGTH = 60


class AttnDecoderRNN(nn.Module):
    def __init__(self, hidden_size, output_size, dropout_p=0.1, max_length=MAX_LENGTH):
        super(AttnDecoderRNN, self).__init__()
        self.hidden_size = hidden_size
        self.output_size = output_size
        self.dropout_p = dropout_p
        self.max_length = max_length

        self.embedding = nn.Embedding(self.output_size, self.hidden_size)
        self.attn = nn.Linear(self.hidden_size * 2, self.max_length)
        self.attn_combine = nn.Linear(self.hidden_size * 2, self.hidden_size)
        self.dropout = nn.Dropout(self.dropout_p)
        self.gru = nn.GRU(self.hidden_size, self.hidden_size)
        self.out = nn.Linear(self.hidden_size, self.output_size)

    def forward(self, input, hidden, encoder_outputs):
        embedded = self.embedding(input).view(1, 1, -1)
        embedded = self.dropout(embedded)

        attn_weights = F.softmax(
            self.attn(torch.cat((embedded[0], hidden[0]), 1)), dim=1)
        attn_applied = torch.bmm(attn_weights.unsqueeze(0),
                                 encoder_outputs.unsqueeze(0))

        output = torch.cat((embedded[0], attn_applied[0]), 1)
        output = self.attn_combine(output).unsqueeze(0)

        output = F.relu(output)
        output, hidden = self.gru(output, hidden)

        output = F.log_softmax(self.out(output[0]), dim=1)
        return output, hidden, attn_weights

    def initHidden(self):
        return torch.zeros(1, 1, self.hidden_size, device=device)


# ## Training

# ### Preparing Data for Training
# 
# To train, for each pair we will need an input tensor (indexes of the words in the input sentence) and target tensor (indexes of the words in the target sentence). While creating these vectors we will append the EOS token to both sequences

# In[ ]:


def indexesFromSentence(lang, sentence):
    return [lang.word2index[word] for word in sentence.split(' ')]


def tensorFromSentence(lang, sentence):
    indexes = indexesFromSentence(lang, sentence)
    indexes.append(EOS_token)
    return torch.tensor(indexes, dtype=torch.long, device=device).view(-1, 1)


def tensorsFromPair(pair):
    input_tensor = tensorFromSentence(input_lang, pair[0])
    target_tensor = tensorFromSentence(output_lang, pair[1])
    return input_tensor, target_tensor


# ### Training the Model
# 
# To train we run the input sentence through the encoder, and keep track of every output and the latest hidden state. Then the decoder is given the <SOS> token as its first input, and the last hidden state of the encoder as its first hidden state.
# 
# “Teacher forcing” is the concept of using the real target outputs as each next input, instead of using the decoder’s guess as the next input. Using teacher forcing causes it to converge faster

# In[ ]:

#
# teacher_forcing_ratio = 0.5
#
#
# def train(input_tensor, target_tensor, encoder, decoder, encoder_optimizer, decoder_optimizer, criterion,
#           max_length=MAX_LENGTH):
#     encoder_hidden = encoder.initHidden()
#
#     encoder_optimizer.zero_grad()
#     decoder_optimizer.zero_grad()
#
#     input_length = input_tensor.size(0)
#     target_length = target_tensor.size(0)
#
#     encoder_outputs = torch.zeros(max_length, encoder.hidden_size, device=device)
#
#     loss = 0
#
#     for ei in range(input_length):
#         encoder_output, encoder_hidden = encoder(
#             input_tensor[ei], encoder_hidden)
#         encoder_outputs[ei] = encoder_output[0, 0]
#
#     decoder_input = torch.tensor([[SOS_token]], device=device)
#
#     decoder_hidden = encoder_hidden
#
#     use_teacher_forcing = True if random.random() < teacher_forcing_ratio else False
#
#     if use_teacher_forcing:
#         # Teacher forcing: Feed the target as the next input
#         for di in range(target_length):
#             decoder_output, decoder_hidden, decoder_attention = decoder(
#                 decoder_input, decoder_hidden, encoder_outputs)
#             loss += criterion(decoder_output, target_tensor[di])
#             decoder_input = target_tensor[di]  # Teacher forcing
#
#     else:
#         # Without teacher forcing: use its own predictions as the next input
#         for di in range(target_length):
#             decoder_output, decoder_hidden, decoder_attention = decoder(
#                 decoder_input, decoder_hidden, encoder_outputs)
#             topv, topi = decoder_output.topk(1)
#             decoder_input = topi.squeeze().detach()  # detach from history as input
#
#             loss += criterion(decoder_output, target_tensor[di])
#             if decoder_input.item() == EOS_token:
#                 break
#
#     loss.backward()
#
#     encoder_optimizer.step()
#     decoder_optimizer.step()
#
#     return loss.item() / target_length


# Helper function to print time elapsed and estimated time remaining given the current time and progress %.

# In[ ]:


# import time
# import math
#
#
# def asMinutes(s):
#     m = math.floor(s / 60)
#     s -= m * 60
#     return '%dm %ds' % (m, s)
#
#
# def timeSince(since, percent):
#     now = time.time()
#     s = now - since
#     es = s / (percent)
#     rs = es - s
#     return '%s (- %s)' % (asMinutes(s), asMinutes(rs))


# The whole training process looks like this:
# 
# - Start a timer
# - Initialize optimizers and criterion
# - Create set of training pairs
# - Start empty losses array for plotting

# In[ ]:


# def trainIters(encoder, decoder, n_iters, print_every=1000, plot_every=100, learning_rate=0.01):
#     start = time.time()
#     plot_losses = []
#     print_loss_total = 0  # Reset every print_every
#     plot_loss_total = 0  # Reset every plot_every
#
#     encoder_optimizer = optim.SGD(encoder.parameters(), lr=learning_rate)
#     decoder_optimizer = optim.SGD(decoder.parameters(), lr=learning_rate)
#     training_pairs = [tensorsFromPair(random.choice(pairs))
#                       for i in range(n_iters)]
#     criterion = nn.NLLLoss()
#
#     for iter in range(1, n_iters + 1):
#         training_pair = training_pairs[iter - 1]
#         input_tensor = training_pair[0]
#         target_tensor = training_pair[1]
#
#         loss = train(input_tensor, target_tensor, encoder,
#                      decoder, encoder_optimizer, decoder_optimizer, criterion)
#         print_loss_total += loss
#         plot_loss_total += loss
#
#         if iter % print_every == 0:
#             print_loss_avg = print_loss_total / print_every
#             print_loss_total = 0
#             print('%s (%d %d%%) %.4f' % (timeSince(start, iter / n_iters),
#                                          iter, iter / n_iters * 100, print_loss_avg))
#
#         if iter % plot_every == 0:
#             plot_loss_avg = plot_loss_total / plot_every
#             plot_losses.append(plot_loss_avg)
#             plot_loss_total = 0
#
#     showPlot(plot_losses)
#

# #### Plotting results
# 
# Plotting is done with matplotlib, using the array of loss values plot_losses saved while training.

# In[ ]:


# import matplotlib.pyplot as plt
#
# plt.switch_backend('agg')
# import matplotlib.ticker as ticker
# import numpy as np
#
#
# def showPlot(points):
#     plt.figure()
#     fig, ax = plt.subplots()
#     # this locator puts ticks at regular intervals
#     loc = ticker.MultipleLocator(base=0.2)
#     ax.yaxis.set_major_locator(loc)
#     plt.plot(points)


# ### Evaluation
# 
# Evaluation is mostly the same as training, but there are no targets so we simply feed the decoder’s predictions back to itself for each step. Every time it predicts a word we add it to the output string, and if it predicts the EOS token we stop there. We also store the decoder’s attention outputs for display later.

# In[ ]:


def evaluate(sentence,input_langauage, max_length=60):
    data_dir = os.getcwd() + '\\translator_data\\models'
    
    if input_langauage == 'eng':
        input_lang, output_lang, pairs = prepareData('chi', 'eng', False)
        
        # encoder = EncoderRNN(input_lang.n_words, hidden_size).to(device)
        # encoder.load_state_dict(torch.load(data_dir + '\\model.encoder.09apr04_en', map_location=device))
        # encoder.eval()

        # decoder = AttnDecoderRNN(hidden_size, output_lang.n_words, dropout_p=0.1).to(device)
        # decoder.load_state_dict(torch.load(data_dir + '\\model.decoder.09apr04_en', map_location=device))
        # decoder.eval()
    else:
        input_lang, output_lang, pairs = prepareData('chi', 'eng', True)
        
    encoder = EncoderRNN(input_lang.n_words, hidden_size).to(device)
    encoder.load_state_dict(torch.load(data_dir + '\\model.encoder.09apr04_ch', map_location=device), strict=False)
    encoder.eval()

    decoder = AttnDecoderRNN(hidden_size, output_lang.n_words, dropout_p=0.1).to(device)
    decoder.load_state_dict(torch.load(data_dir + '\\model.decoder.09apr04_ch', map_location=device), strict=False)
    decoder.eval()
        

    with torch.no_grad():
        input_tensor = tensorFromSentence(input_lang, sentence)
        input_length = input_tensor.size()[0]
        encoder_hidden = encoder.initHidden()

        encoder_outputs = torch.zeros(max_length, encoder.hidden_size, device=device)

        for ei in range(input_length):
            encoder_output, encoder_hidden = encoder(input_tensor[ei],
                                                     encoder_hidden)
            encoder_outputs[ei] += encoder_output[0, 0]

        decoder_input = torch.tensor([[SOS_token]], device=device)  # SOS

        decoder_hidden = encoder_hidden

        decoded_words = []
        decoder_attentions = torch.zeros(max_length, max_length)

        for di in range(max_length):
            decoder_output, decoder_hidden, decoder_attention = decoder(
                decoder_input, decoder_hidden, encoder_outputs)
            decoder_attentions[di] = decoder_attention.data
            topv, topi = decoder_output.data.topk(1)
            if topi.item() == EOS_token:
                # decoded_words.append('<Promise>')
                break
            else:
                decoded_words.append(output_lang.index2word[topi.item()])

            decoder_input = topi.squeeze().detach()

        return decoded_words


# We can evaluate random sentences from the training set and print out the input, target, and output to make some subjective quality judgements:

# In[ ]:


# def evaluateRandomly(encoder, decoder, n=5):
#     for i in range(n):
#         pair = random.choice(pairs)
#         print('>', pair[0])
#         print('=', pair[1])
#         output_words, attentions = evaluate(encoder, decoder, pair[0])
#         output_sentence = ' '.join(output_words)
#         print('<', output_sentence)
#         print('')


# ### Training and Evaluating
# 
# With all these helper functions in place, makes it easier to run multiple experiments.

# In[ ]:


hidden_size = 512
# encoder1 = EncoderRNN(input_lang.n_words, hidden_size).to(device)
# attn_decoder1 = AttnDecoderRNN(hidden_size, output_lang.n_words, dropout_p=0.1).to(device)
#
# trainIters(encoder1, attn_decoder1, 7000, print_every=700)

# In[ ]:


# evaluateRandomly(encoder1, attn_decoder1)
#
# # ### Save model
#
# # In[ ]:
#
#
# torch.save(encoder1.state_dict(), 'models/model.encoder.01apr003')
# torch.save(attn_decoder1.state_dict(), 'models/model.decoder.01apr003')

# ### Load model

# In[ ]:


# encoder1 = EncoderRNN(input_lang.n_words, hidden_size).to(device)
# encoder1.load_state_dict(torch.load('models/model.encoder.01apr003'))
# encoder1.eval()
#
# # In[ ]:
#
#
# attn_decoder1 = AttnDecoderRNN(hidden_size, output_lang.n_words, dropout_p=0.1).to(device)
# attn_decoder1.load_state_dict(torch.load('models/model.decoder.01apr003'))
# attn_decoder1.eval()
#
# # In[ ]:
#
#
# evaluateRandomly(encoder1, attn_decoder1)

# In[ ]:


# evaluate("ngakhale dziko la malawi limatulutsa mpweya umene umaononga thambo loteteza mlengalenga")
