from argparse import ArgumentParser
import torch
import os

from models import RgModel, ConvRgModel, RecurrentRgModel, Ensemble
from sampler import build_dataset_iter
from inference import Inference
from utils import logger
from trainer import Trainer
from data import prep_data


def get_parser():
    parser = ArgumentParser(description='Train/Use an Information Extractor '
                            'model, on Rotowire data.')

    group = parser.add_argument_group('Script behavior')
    group.add_argument('--just-eval', dest='just_eval', default=False,
                       action="store_true", help='just run evaluation script')
    group.add_argument('--test', dest='test', default=False,
                       action="store_true", help='use test data')
    group.add_argument('--show-correctness', dest="show_correctness",
                       action='store_true', help="When doing inference, add a "
                                                 "sign |RIGHT or |WRONG to "
                                                 "generated tuples")

    group = parser.add_argument_group('File system')
    group.add_argument('--datafile', dest='datafile',
                       help='path to hdf5 file containing train/val data')
    group.add_argument('--preddata', dest='preddata', default=None,
                       help='path to hdf5 file containing candidate relations '
                            'from generated data')
    group.add_argument('--save-directory', dest='save_directory', default='',
                       help='path to a directory where to model should be saved')
    group.add_argument('--eval-models', dest='eval_models', default=None,
                       help='path to a directory with trained extractor models')
    group.add_argument('--vocab-prefix', dest='vocab_prefix', default='',
                       help='prefix of .dict and .labels files')

    group = parser.add_argument_group('Evaluation options')
    group.add_argument('--ignore-idx', dest='ignore_idx', default=None, type=int,
                       help="The index of NONE label in your .label file")
    group.add_argument('--average-func', dest='average_func', default='arithmetic',
                       choices=['geometric', 'arithmetic'],
                       help='Use geometric/arithmetic mean to ensemble models')

    group = parser.add_argument_group('Training options')
    group.add_argument('--num-epochs', dest='num_epochs', default=10, type=int,
                       help='Number of training epochs')
    group.add_argument('--gpu', dest='gpu', default=None, type=int, help='gpu idx')
    group.add_argument('--batch-size', dest='batch_size', default=32, type=int,
                       help='batch size')
    group.add_argument('--lr', dest='lr', default=0.7, type=float,
                       help='learning rate')
    group.add_argument('--lr-decay', dest='lr_decay', default=0.5, type=float,
                       help='decay factor')
    group.add_argument('--max-grad-norm', dest='max_grad_norm', default=5,
                       help='clip grads so they do not exceed this')
    group.add_argument('--seed', dest='seed', default=3435, type=int,
                       help='Random seed')

    group = parser.add_argument_group('Model configuration')
    group.add_argument('--model', dest='model', choices=['lstm', 'conv'])
    group.add_argument('--embedding-size', dest='embedding_size', type=int,
                       default=200, help="Dimensions of embedding space")
    group.add_argument('--hidden-dim', dest='hidden_dim', default=500, type=int,
                       help="Hidden dimension of the model")
    group.add_argument('--dropout', dest='dropout', default=0.5, type=float,
                       help='if >0 use dropout regularization')

    group = parser.add_argument_group('Conv specific configuration')
    group.add_argument('--num-filters', dest='num_filters', default=200,
                       type=int, help='number of convolutional filters')

    return parser


def configure_process(args, logger=None):
    """
    Sets the seed and device for the current run
    """
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed(args.seed)
    device = torch.device('cpu' if args.gpu is None else f'cuda:{args.gpu}')

    if logger is not None:
        logger.info(f'Current run is using seed={args.seed} and {device=}')

    return device


def main(args=None):
    parser = get_parser()
    args = parser.parse_args(args) if args else parser.parse_args()

    device = configure_process(args, logger)

    datasets, min_dists, paddings, nlabels = prep_data(args.datafile,
                                                       args.preddata,
                                                       args.test,
                                                       args.just_eval)
    train, val, test = datasets

    datakwargs = {'batch_size': args.batch_size,
                  'vocab_sizes': paddings,
                  'device': device}
    loaders = [
        build_dataset_iter(train, **datakwargs),
        build_dataset_iter(val, **datakwargs, is_eval=True),
        build_dataset_iter(test, **datakwargs, is_eval=True)
    ]

    emb_sizes = [args.embedding_size,
                 args.embedding_size // 2,
                 args.embedding_size // 2]
    vocab_sizes = [p + 1 for p in paddings]

    if args.just_eval:

        # Load models
        models = [
            RgModel.from_file(os.path.join(args.eval_models, filename))
            for filename in os.listdir(args.eval_models)
            if filename.endswith('.pt')
        ]

        model = Ensemble(models, average_func=args.average_func).to(device)

        min_entdist, min_numdist = min_dists

        inference = Inference(args.vocab_prefix, min_entdist, min_numdist,
                              ignore_idx=args.ignore_idx,
                              show_correctness=args.show_correctness,
                              logger=None)

        inference.run(loaders[2], model, f'{args.preddata}-tuples.txt')

        return

    # Building models
    if args.model == 'lstm':
        model = RecurrentRgModel(vocab_sizes=vocab_sizes,
                                 emb_sizes=emb_sizes,
                                 hidden_dim=args.hidden_dim,
                                 nlabels=nlabels,
                                 dropout=args.dropout)
        module_names = ['embeddings', 'rnn', 'linear']
    else:
        model = ConvRgModel(vocab_sizes=vocab_sizes,
                            emb_sizes=emb_sizes,
                            num_filters=args.num_filters,
                            hidden_dim=args.hidden_dim,
                            nlabels=nlabels,
                            dropout=args.dropout)
        module_names=['embeddings', 'convolutions', 'linear']

    logger.info(model)
    model.count_parameters(log=logger.info, module_names=module_names)

    model.to(device)
    trainer = Trainer(
        paddings=paddings,
        logger=logger,
        save_directory=args.save_directory,
        max_grad_norm=args.max_grad_norm,
        ignore_idx=args.ignore_idx)

    trainer.train(model,
                  loaders,
                  n_epochs=args.num_epochs,
                  lr=args.lr,
                  lr_decay=args.lr_decay)


if __name__ == '__main__':
    main()
