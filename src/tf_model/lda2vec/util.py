import numpy as np
import pandas as pd
import pickle
import pyLDAvis
# noinspection PyProtectedMember
from sklearn.utils import shuffle
import tensorflow as tf


def dirichlet_likelihood(weights, alpha=None):
    """
    Calculate the log likelihood of the observed topic proportions.
    A negative likelihood is more likely than a negative likelihood.

    :param weights: Unnormalized weight vector. The vector
            will be passed through a softmax function that will map the input
            onto a probability simplex.
    :param alpha: (float) The Dirichlet concentration parameter. Alpha
            greater than 1.0 results in very dense topic weights such
            that each document belongs to many topics. Alpha < 1.0 results
            in sparser topic weights. The default is to set alpha to
            1.0 / n_topics, effectively enforcing the prior belief that a
            document belong to every topics at once.
    :return: Output loss variable.
    """
    n_topics = weights.get_shape()[1].value
    if alpha is None:
        alpha = 1.0 / n_topics

    log_proportions = tf.nn.log_softmax(weights)
    loss = (alpha - 1.0) * log_proportions
    return tf.reduce_sum(loss)


def prepare_topics(weights, factors, word_vectors, vocab, temperature=1.0,
                   doc_lengths=None, term_frequency=None, normalize=False):
    """
    Collects a dictionary of word, document and topic distributions.

    :param weights: (array[float]) This must be an array of unnormalized log-odds of document-to-topic
        weights. Shape should be [n_documents, n_topics]
    :param factors: (array[float]) Should be an array of topic vectors. These topic vectors live in the
        same space as word vectors and will be used to find the most similar
        words to each topic. Shape should be [n_topics, n_dim].
    :param word_vectors: (array[float]) This must be a matrix of word vectors. Should be of shape
        [n_words, n_dim].
    :param vocab: (list[str]) These must be the strings for words corresponding to
        indices [0, n_words].
    :param temperature: (float) Used to calculate the log probability of a word. Higher
        temperatures make more rare words more likely.
    :param doc_lengths: (array[int]) An array indicating the number of words in the nth document.
        Must be of shape [n_documents]. Required by pyLDAvis.
    :param term_frequency: (array[int]) An array indicating the overall number of times each token appears
        in the corpus. Must be of shape [n_words]. Required by pyLDAvis.
    :param normalize: (bool) If true, then normalize word vectors
    :return:
        data: (dict) This dictionary is readily consumed by pyLDAVis for topic
        visualization.
    """
    # Map each factor vector to a word
    topic_to_word = []
    assert len(vocab) == word_vectors.shape[0], 'Vocabulary size did not match size of word vectors'
    if normalize:
        word_vectors /= np.linalg.norm(word_vectors, axis=1)[:, None]

    for factor_vector in factors:
        factor_to_word = prob_words(factor_vector, word_vectors, temperature=temperature)
        topic_to_word.append(np.ravel(factor_to_word))

    topic_to_word = np.array(topic_to_word)
    assert np.allclose(np.sum(topic_to_word, axis=1), 1), 'Not all rows in `topic_to_word` sum to 1'

    # Collect document-to-topic distributions, e.g. theta
    doc_to_topic = _softmax_2d(weights)
    assert np.allclose(np.sum(doc_to_topic, axis=1), 1), 'Not all rows in `doc_to_topic` sum to 1'
    return {
        'topic_term_dists': topic_to_word,
        'doc_topic_dists': doc_to_topic,
        'doc_lengths': doc_lengths,
        'vocab': vocab,
        'term_frequency': term_frequency
    }


def prob_words(context, vocab, temperature=1.0):
    """This calculates a softmax over the vocabulary as a function of the dot product of context and word."""
    dot = np.dot(vocab, context)
    return _softmax(dot / temperature)


def load_preprocessed_data(data_path, load_embed_mat=False, shuffle_data=True):
    """
    Load all preprocessed data.

    Also load embedding matrix if saved in `data_path`.

    :param data_path: (PosixPath) where data is stored. Should be same path as passed
           to preprocessor.
    :param load_embed_mat: (bool, optional) if True, load `embedding_matrix.npy`
           found in `data_path`.
    :param shuffle_data: (bool, optional) if True, will shuffle the skipgrams
           DataFrame when loaded. Otherwise leave it in original order.
    :return:
    """
    # Reload all data
    with open(data_path / 'idx_to_word.pkl', 'rb') as f:
        idx_to_word = pickle.load(f)

    with open(data_path / 'word_to_idx.pkl', 'rb') as f:
        word_to_idx = pickle.load(f)

    freqs = np.load(data_path / 'freqs.npy').tolist()
    df = pd.read_csv(data_path / 'skipgrams.txt', sep='\t', header=None)

    # Extract data arrays from DataFrame
    pivot_ids = df[0].values
    target_ids = df[1].values
    doc_ids = df[2].values

    if shuffle_data:
        pivot_ids, target_ids, doc_ids = shuffle(pivot_ids, target_ids, doc_ids, random_state=0)

    if load_embed_mat:
        embed_mat = np.load(data_path / 'embedding_matrix.npy')
        return idx_to_word, word_to_idx, freqs, pivot_ids, target_ids, doc_ids, embed_mat

    return idx_to_word, word_to_idx, freqs, pivot_ids, target_ids, doc_ids


def generate_ldavis_data(data_path, model, idx_to_word, freqs, vocab_size):
    """
    This function will launch a locally hosted session of pyLDAvis to visualize the results of our model.

    :param data_path: (PosixPath) data location
    :param model: TensorFlow model
    :param idx_to_word: (dict) index-to-word mapping
    :param freqs: (list) frequency counts of each token
    :param vocab_size: (int) size of vocabulary
    :return:
    """
    doc_embed = model.sess.run(model.doc_embedding)
    topic_embed = model.sess.run(model.topic_embedding)
    word_embed = model.sess.run(model.word_embedding)

    # Extract all unique words in order of index: 1 - (vocab_size + 1)
    # NOTE! Keras Tokenizer indexes from 1, 0 is reserved for PAD token
    vocabulary = ['<PAD>']
    for i in range(1, vocab_size):
        vocabulary.append(idx_to_word[i])

    # Read document lengths
    doc_lengths = np.load(data_path / 'doc_lengths.npy')

    # The `prepare_topics` function is a direct copy from Chris Moody
    vis_data = prepare_topics(doc_embed, topic_embed, word_embed, np.array(vocabulary), doc_lengths=doc_lengths,
                              term_frequency=freqs, normalize=True)
    prepared_vis_data = pyLDAvis.prepare(**vis_data)
    pyLDAvis.show(prepared_vis_data)


def _softmax(x):
    e_x = np.exp(x - np.max(x))
    return e_x / e_x.sum()


def _softmax_2d(x):
    y = x - x.max(axis=1, keepdims=True)
    np.exp(y, out=y)
    y /= y.sum(axis=1, keepdims=True)
    return y
