import argparse
import sys
from operator import itemgetter

import torch

from data_loader import DataLoader
import data_loader
from simple_nmt.seq2seq import Seq2Seq


def define_argparser():
    p = argparse.ArgumentParser()

    p.add_argument('--model',
                   required=True,
                   help='Model file name to use'
                   )
    p.add_argument('--gpu_id',
                   type=int,
                   default=-1,
                   help='GPU ID to use. -1 for CPU. Default=-1'
                   )

    p.add_argument('--batch_size',
                   type=int,
                   default=128,
                   help='Mini batch size for parallel inference. Default=128'
                   )
    p.add_argument('--max_length',
                   type=int,
                   default=255,
                   help='Maximum sequence length for inference. Default=255'
                   )
    p.add_argument('--n_best',
                   type=int,
                   default=1,
                   help='Number of best inference result per sample. Default=1'
                   )
    p.add_argument('--beam_size',
                   type=int,
                   default=5,
                   help='Beam size for beam search. Default=5'
                   )

    config = p.parse_args()

    return config


def read_text():
    # This method gets sentences from standard input and tokenize those.
    lines = []

    for line in sys.stdin:
        if line.strip() != '':
            lines += [line.strip().split(' ')]

    return lines


def to_text(indice, vocab):
    # This method converts index to word to show the translation result.
    lines = []

    for i in range(len(indice)):
        line = []
        for j in range(len(indice[i])):
            index = indice[i][j]

            if index == data_loader.EOS:
                # line += ['<EOS>']
                break
            else:
                line += [vocab.itos[index]]

        line = ' '.join(line)
        lines += [line]

    return lines


if __name__ == '__main__':
    config = define_argparser()

    # Load saved model.
    saved_data = torch.load(config.model)

    # Load configuration setting in training.
    train_config = saved_data['config']
    # Load vocabularies from the model.
    src_vocab = saved_data['src_vocab']
    tgt_vocab = saved_data['tgt_vocab']

    # Initialize dataloader, but we don't need to read training & test corpus.
    # What we need is just load vocabularies from the previously trained model.
    loader = DataLoader()
    loader.load_vocab(src_vocab, tgt_vocab)
    input_size = len(loader.src.vocab)
    output_size = len(loader.tgt.vocab)

    # Declare sequence-to-sequence model.
    model = Seq2Seq(input_size,
                    train_config.word_vec_dim,
                    train_config.hidden_size,
                    output_size,
                    n_layers=train_config.n_layers,
                    dropout_p=train_config.dropout
                    )
    model.load_state_dict(saved_data['model'])  # Load weight parameters from the trained model.
    model.eval()  # We need to turn-on the evaluation mode, which turns off all drop-outs.

    # We don't need to draw a computation graph, because we will have only inferences.
    torch.set_grad_enabled(False)

    # Put models to device if it is necessary.
    if config.gpu_id >= 0:
        model.cuda(config.gpu_id)

    # Get sentences from standard input.
    lines = read_text()

    with torch.no_grad():  # Also, declare again to prevent to get gradients.
        while len(lines) > 0:
            # Since packed_sequence must be sorted by decreasing order of length,
            # sorting by length in mini-batch should be restored by original order.
            # Therefore, we need to memorize the original index of the sentence.
            sorted_lines = lines[:config.batch_size]
            lines = lines[config.batch_size:]
            lengths = [len(_) for _ in sorted_lines]
            orders = [i for i in range(len(sorted_lines))]

            sorted_tuples = sorted(zip(sorted_lines, lengths, orders), 
                                   key=itemgetter(1),
                                   reverse=True
                                   )
            sorted_lines = [sorted_tuples[i][0] for i in range(len(sorted_tuples))]
            lengths = [sorted_tuples[i][1] for i in range(len(sorted_tuples))]
            orders = [sorted_tuples[i][2] for i in range(len(sorted_tuples))]

            # Converts string to list of index.
            x = loader.src.numericalize(loader.src.pad(sorted_lines),
                                        device='cuda:%d' % config.gpu_id if config.gpu_id >= 0 else 'cpu'
                                        )

            if config.beam_size == 1:
                # Take inference for non-parallel beam-search.
                y_hat, indice = model.search(x)
                output = to_text(indice, loader.tgt.vocab)

                sorted_tuples = sorted(zip(output, orders), key=itemgetter(1))
                output = [sorted_tuples[i][0] for i in range(len(sorted_tuples))]

                sys.stdout.write('\n'.join(output) + '\n')
            else:
                # Take mini-batch parallelized beam search.
                batch_indice, _ = model.batch_beam_search(x,
                                                          beam_size=config.beam_size,
                                                          max_length=config.max_length,
                                                          n_best=config.n_best
                                                          )

                # Restore the original orders.
                output = []
                for i in range(len(batch_indice)):
                    output += [to_text(batch_indice[i], loader.tgt.vocab)]
                sorted_tuples = sorted(zip(output, orders), key=itemgetter(1))
                output = [sorted_tuples[i][0] for i in range(len(sorted_tuples))]

                for i in range(len(output)):
                    sys.stdout.write('\n'.join(output[i]) + '\n')
