"""Training code for spirals dataset."""
from __future__ import division
from __future__ import print_function
from __future__ import absolute_import

import os, argparse, yaml

import numpy as np
import torch
import matplotlib.pyplot as plt
from matplotlib.collections import EllipseCollection

from datasets.spirals import SpiralsDataset
import trainer

class SpiralsTrainer(trainer.Trainer):
    """Class for training on noisy 2D spirals."""

    def build_model(self, constructor, args):
        """Construct model using provided constructor."""
        dims = {'spiral-x': 1, 'spiral-y': 1}
        model = constructor(args.modalities,
                            dims=(dims[m] for m in args.modalities),
                            z_dim=5, h_dim=20,
                            device=args.device, **args.model_args)
        return model

    def default_args(self, args):
        """Fill unspecified args with default values."""
        # Default reconstruction loss multipliers
        if args.rec_mults is None:
            dims = self.model.dims
            corrupt_mult = 1 / (1 - args.corrupt.get('uniform', 0.0))
            args.rec_mults = {m : ((1.0 / dims[m]) / len(args.modalities)
                                   * corrupt_mult)
                              for m in args.modalities}
        return args

    def load_data(self, modalities, args):
        """Loads data for specified modalities."""
        print("Loading data...")
        data_dir = os.path.abspath(args.data_dir)
        train_data = SpiralsDataset(modalities, data_dir, args.train_subdir,
                                    truncate=True, item_as_dict=True)
        test_data = SpiralsDataset(modalities, data_dir, args.test_subdir,
                                   truncate=True, item_as_dict=True)
        print("Done.")
        if len(args.normalize) > 0:
            print("Normalizing ", args.normalize, "...")
            # Normalize test data using training data as reference
            test_data.normalize_(modalities=args.normalize,
                                 ref_data=train_data)
            # Normalize training data in-place
            train_data.normalize_(modalities=args.normalize)
        return train_data, test_data
    
    def compute_metrics(self, model, infer, prior, recon,
                        targets, mask, lengths, order, args):
        """Compute evaluation metrics from batch of inputs and outputs."""    
        metrics = dict()
        if type(lengths) != torch.tensor:
            lengths = torch.tensor(lengths).float().to(args.device)
        # Compute and store KLD and reconstruction losses
        metrics['kld_loss'] = model.kld_loss(infer, prior, mask)
        metrics['rec_loss'] = model.rec_loss(targets, recon, mask,
                                             args.rec_mults)
        # Compute mean squared error in 2D space for each time-step
        mse = sum([(recon[m][0]-targets[m]).pow(2) for m in recon.keys()])
        mse = mse.sum(dim=range(2, mse.dim()))
        # Average across timesteps, for each sequence
        def time_avg(val):
            val[1 - mask.squeeze(-1)] = 0.0
            return val.sum(dim = 0) / lengths
        metrics['mse'] = time_avg(mse)[order].tolist()    
        return metrics

    def summarize_metrics(self, metrics, n_timesteps):
        """Summarize and print metrics across dataset."""
        summary = dict()
        for key, val in metrics.items():
            if type(val) is list:
                # Compute mean and std dev. of metric over sequences
                summary[key] = np.mean(val)
                summary[key + '_std'] = np.std(val)
            else:
                # Average over all timesteps
                summary[key] = val / n_timesteps
        print(('Evaluation\tKLD: {:7.1f}\tRecon: {:7.1f}\t' +
               'MSE: {:6.3f} +-{:2.3f}')\
              .format(summary['kld_loss'], summary['rec_loss'],
                      summary['mse'], summary['mse_std']))
        return summary

    def visualize(self, results, metric, args):
        """Plots predictions against truth for representative fits."""
        reference = results['targets']
        observed = results['inputs']
        predicted = results['recon']

        # Select top 4 and bottom 4 predictions
        sel_idx = np.concatenate((np.argsort(metric)[:4],
                                  np.argsort(metric)[-4:][::-1]))
        sel_metric = [metric[i] for i in sel_idx]
        sel_true = [reference['metadata'][i][:,0:2] for i in sel_idx]
        sel_true = [(arr[:,0], arr[:,1]) for arr in sel_true]
        sel_data = [(reference['spiral-x'][i], reference['spiral-y'][i])
                    for i in sel_idx]
        sel_obsv = [(observed['spiral-x'][i], observed['spiral-y'][i])
                   for i in sel_idx]
        sel_pred = [(predicted['spiral-x'][i][:,0],
                     predicted['spiral-y'][i][:,0])
                    for i in sel_idx]
        sel_rng = [(predicted['spiral-x'][i][:,1],
                    predicted['spiral-y'][i][:,1])
                    for i in sel_idx]

        # Create figure to visualize predictions
        if not hasattr(args, 'fig'):
            args.fig, args.axes = plt.subplots(4, 2, figsize=(4,8),
                                               subplot_kw={'aspect': 'equal'})
        else:
            plt.figure(args.fig.number)
        axes = args.axes

        # Set current figure
        plt.figure(args.fig.number)
        for i in range(len(sel_idx)):
            axis = args.axes[(i % 4),(i // 4)]
            # Plot spiral
            self.plot_spiral(axis, sel_true[i], sel_data[i],
                             sel_obsv[i], sel_pred[i], sel_rng[i])
            # Set title as metric
            axis.set_title("Metric = {:0.3f}".format(sel_metric[i]))
            axis.set_xlabel("Spiral {:03d}".format(sel_idx[i]))

        plt.tight_layout()
        plt.draw()
        if args.eval_set is not None:
            fig_path = os.path.join(args.save_dir, args.eval_set + '.pdf')
            plt.savefig(fig_path)
        plt.pause(1.0 if args.test else 0.001)

    def plot_spiral(self, axis, true, data, obsv, pred, rng):
        """Plots a single spiral on provided axis."""
        axis.cla()
        # Plot 95% confidence ellipses
        ec = EllipseCollection(1.96*rng[0], 1.96*rng[1], (0,), units='x',
                               facecolors=('c',), alpha=0.25,
                               offsets=np.column_stack(pred),
                               transOffset=axis.transData)
        axis.add_collection(ec)

        # Plot ground truth
        axis.plot(true[0], true[1], 'b-', linewidth=1.5)

        # Plot observations (blue = both, pink = x-only, yellow = y-only)
        if (np.isnan(obsv[0]) != np.isnan(obsv[1])).any():
            axis.plot(obsv[0], data[1], '<', markersize=2, color='#fe46a5')
            axis.plot(data[0], obsv[1], 'v', markersize=2, color='#fec615')
        axis.plot(obsv[0], obsv[1], 'bo', markersize=3)

        # Plot predictions
        axis.plot(pred[0], pred[1], '-', linewidth=1.5, color='#04d8b2')

        # Set limits
        axis.set_xlim(-4, 4)
        axis.set_ylim(-4, 4)

    def save_results(self, results, args):
        pass
        
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--modalities', type=str, nargs='+',
                        default=['spiral-x', 'spiral-y'],
                        help='input modalities (default: all)')
    parser.add_argument('--model', type=str, default='dmm', metavar='S',
                        help='name of model to train (default: dmm)')
    parser.add_argument('--model_args', type=yaml.safe_load, default=dict(),
                        help='additional model arguments as yaml dict')
    parser.add_argument('--train_args', type=yaml.safe_load, default=dict(),
                        help='additional training arguments as yaml dict')
    parser.add_argument('--eval_args', type=yaml.safe_load, default=dict(),
                        help='additional evaluation arguments as yaml dict')
    parser.add_argument('--batch_size', type=int, default=100, metavar='N',
                        help='input batch size for training (default: 100)')
    parser.add_argument('--split', type=int, default=1, metavar='N',
                        help='sections to split each video into (default: 1)')
    parser.add_argument('--bylen', action='store_true', default=False,
                        help='whether to split by length')
    parser.add_argument('--epochs', type=int, default=100, metavar='N',
                        help='number of epochs to train (default: 100)')
    parser.add_argument('--lr', type=float, default=1e-4, metavar='LR',
                        help='learning rate (default: 1e-4)')
    parser.add_argument('--w_decay', type=float, default=1e-4, metavar='F',
                        help='Adam weight decay (default: 1e-4)')
    parser.add_argument('--clip_grad', type=float, default=None, metavar='F',
                        help='clip gradients to this norm (default: None)')
    parser.add_argument('--seed', type=int, default=1, metavar='N',
                        help='random seed (default: 1)')
    parser.add_argument('--kld_mult', type=float, default=1.0, metavar='F',
                        help='max kld loss multiplier (default: 1.0)')
    parser.add_argument('--rec_mults', type=yaml.safe_load, default=None,
                        help='reconstruction loss multiplier (default: 1/dim)')
    parser.add_argument('--kld_anneal', type=int, default=100, metavar='N',
                        help='epochs to increase kld_mult over (default: 100)')
    parser.add_argument('--burst_frac', type=float, default=0.1, metavar='F',
                        help='burst error rate during training (default: 0.1)')
    parser.add_argument('--drop_frac', type=float, default=0.5, metavar='F',
                        help='fraction of data to randomly drop at test time')
    parser.add_argument('--start_frac', type=float, default=0.25, metavar='F',
                        help='fraction of test trajectory to begin at')
    parser.add_argument('--stop_frac', type=float, default=0.75, metavar='F',
                        help='fraction of test trajectory to stop at')
    parser.add_argument('--drop_mods', type=str, default=[], nargs='+',
                        help='modalities to delete at test (default: none')
    parser.add_argument('--keep_mods', type=str, default=[], nargs='+',
                        help='modalities to retain at test (default: none')
    parser.add_argument('--eval_mods', type=str, default=None, nargs='+',
                        help='modalities to evaluate at test (default: none')
    parser.add_argument('--eval_metric', type=str, default='mse',
                        help='metric to track best model (default: mse)')
    parser.add_argument('--viz_metric', type=str, default='mse',
                        help='metric for visualization (default: mse)')
    parser.add_argument('--log_freq', type=int, default=5, metavar='N',
                        help='print loss N times every epoch (default: 5)')
    parser.add_argument('--eval_freq', type=int, default=10, metavar='N',
                        help='evaluate every N epochs (default: 10)')
    parser.add_argument('--save_freq', type=int, default=10, metavar='N',
                        help='save every N epochs (default: 10)')
    parser.add_argument('--device', type=str, default='cuda:0',
                        help='device to use (default: cuda:0 if available)')
    parser.add_argument('--anomaly_check', action='store_true', default=False,
                        help='check for gradient anomalies (default: false)')
    parser.add_argument('--visualize', action='store_true', default=False,
                        help='flag to visualize predictions (default: false)')
    parser.add_argument('--gradients', action='store_true', default=False,
                        help='flag to plot gradients (default: false)')
    parser.add_argument('--normalize', type=str, default=[], nargs='+',
                        help='modalities to normalize (default: [])')
    parser.add_argument('--corrupt', type=yaml.safe_load, default=dict(),
                        help='options to corrupt training data')
    parser.add_argument('--test', action='store_true', default=False,
                        help='evaluate without training (default: false)')
    parser.add_argument('--load', type=str, default=None,
                        help='path to trained model (either resume or test)')
    parser.add_argument('--find_best', action='store_true', default=False,
                        help='find best model in save directory')
    parser.add_argument('--data_dir', type=str, default="./datasets/spirals",
                        help='path to data base directory')
    parser.add_argument('--save_dir', type=str, default="./spirals_save",
                        help='path to save models and predictions')
    parser.add_argument('--train_subdir', type=str, default='train',
                        help='training data subdirectory')
    parser.add_argument('--test_subdir', type=str, default='test',
                        help='testing data subdirectory')
    args = parser.parse_args()
    trainer = SpiralsTrainer(args)
    trainer.run(args)
