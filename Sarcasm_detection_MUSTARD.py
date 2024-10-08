import os
os.environ['CUDA_VISIBLE_DEVICES'] = '1'
import numpy as np
import torch
torch.set_printoptions(profile="full")
from torch.utils.data import DataLoader
from torch.utils.data.sampler import SubsetRandomSampler
import torch.optim as optim
import warnings
import argparse
import time
import random

warnings.filterwarnings('ignore')

from sklearn.metrics import f1_score, accuracy_score, \
    classification_report

from model.MUSTARD_model.model_emotion_with_gat import  MUSTARDBiModelTri as MUSTARDBiModelTri_test
from model.MUSTARD_model.model_emotion_with_gat import  MaskedNLLLoss
from model.MUSTARD_model.dataloader_emotion import MUSTARDDataset

def seed_torch(seed):
    seed = int(seed)
    random.seed(seed)
    os.environ['PYTHONHASHSEED'] = str(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.enabled = False

def get_train_valid_sampler(trainset, valid=0.1):
    size = len(trainset)
    idx = list(range(size))
    split = int(valid * size)
    return SubsetRandomSampler(idx[split:]), SubsetRandomSampler(idx[:split])


def get_MUSTARD_loaders(path, batch_size=32, valid=0.2, num_workers=0, pin_memory=False):

    trainset = MUSTARDDataset(path=path, flag='train')
    train_loader = DataLoader(trainset,
                              batch_size=batch_size,
                              collate_fn=trainset.collate_fn,
                              num_workers=num_workers,
                              pin_memory=pin_memory
                              )
    validset = MUSTARDDataset(path=path, flag='valid')
    valid_loader = DataLoader(validset,
                              batch_size=batch_size,
                              #sampler=valid_sampler,
                              collate_fn=trainset.collate_fn,
                              num_workers=num_workers,
                              pin_memory=pin_memory
                              )
    testset = MUSTARDDataset(path=path, flag='test')
    test_loader = DataLoader(testset,
                             batch_size=batch_size,
                             collate_fn=testset.collate_fn,
                             num_workers=num_workers,
                             pin_memory=pin_memory)
    return train_loader, valid_loader, test_loader


def train_or_eval_model(model, sarcasm_loss_function,implicit_loss_function,explicit_loss_function, dataloader, epoch, optimizer=None, train=False):
    sarcasms_losses = []
    sarcasms_preds = []
    sarcasms_labels = []
    implicit_losses = []
    implicit_preds = []
    implicit_labels = []
    explicit_losses = []
    explicit_preds = []
    explicit_labels = []
    masks = []
    alphas, alphas_f, alphas_b, vids = [], [], [], []
    assert not train or optimizer != None
    if train:
        model.train()
    else:
        model.eval()

    for data in dataloader:
        if train:
            optimizer.zero_grad()

        textf, texte, visuf, visue, acouf, acoue, qmask, umask, sarcasms_label, implicit_label, explicit_label = [d.cuda() for d in data[:-1]] if cuda else data[:-1]
        sarcasms_labels_ = sarcasms_label.view(-1)  # batch*seq_len
        implicit_labels_ = implicit_label.view(-1)
        explicit_labels_ = explicit_label.view(-1)
        sarcasm_log_prob,implicit_log_prob, explicit_log_prob, alpha, alpha_f, alpha_b = model(textf, texte, visuf, visue, acouf, acoue, qmask, umask)  # seq_len, batch, n_classes

        sarcasm_lp_ = sarcasm_log_prob.transpose(0, 1).contiguous().view(-1, sarcasm_log_prob.size()[2])  # batch*seq_len, n_classes
        implicit_lp_ = implicit_log_prob.transpose(0, 1).contiguous().view(-1, implicit_log_prob.size()[2])
        explicit_lp_ = explicit_log_prob.transpose(0, 1).contiguous().view(-1, explicit_log_prob.size()[2])

        sarcasms_loss = sarcasm_loss_function(sarcasm_lp_, sarcasms_labels_, umask)
        sarcasms_pred_ = torch.argmax(sarcasm_lp_, 1)  # batch*seq_len
        sarcasms_preds.append(sarcasms_pred_.data.cpu().numpy())
        sarcasms_labels.append(sarcasms_labels_.data.cpu().numpy())
        masks.append(umask.view(-1).cpu().numpy())

        implicit_loss = implicit_loss_function(implicit_lp_, implicit_labels_, umask)
        implicit_pred_ = torch.argmax(implicit_lp_, 1)  # batch*seq_len
        implicit_preds.append(implicit_pred_.data.cpu().numpy())
        implicit_labels.append(implicit_labels_.data.cpu().numpy())

        explicit_loss = explicit_loss_function(explicit_lp_, explicit_labels_, umask)
        explicit_pred_ = torch.argmax(explicit_lp_, 1)  # batch*seq_len
        explicit_preds.append(explicit_pred_.data.cpu().numpy())
        explicit_labels.append(explicit_labels_.data.cpu().numpy())

        sarcasms_losses.append(sarcasms_loss.item() * masks[-1].sum())
        implicit_losses.append(implicit_loss.item() * masks[-1].sum())
        explicit_losses.append(explicit_loss.item() * masks[-1].sum())

        three_loss = (2 / 5) * sarcasms_loss + (2 / 5) * implicit_loss + (1 / 5) * explicit_loss

        if train:
            three_loss.backward()
            optimizer.step()

    if sarcasms_preds != []:
        sarcasms_preds = np.concatenate(sarcasms_preds)
        sarcasms_labels = np.concatenate(sarcasms_labels)
        masks = np.concatenate(masks)

        implicit_preds = np.concatenate(implicit_preds)
        implicit_labels = np.concatenate(implicit_labels)

        explicit_preds = np.concatenate(explicit_preds)
        explicit_labels = np.concatenate(explicit_labels)
    else:
        return float('nan'), float('nan'), [], [], [], float('nan'), []

    sarcasms_avg_loss = round(np.sum(sarcasms_losses) / np.sum(masks), 4)
    sarcasms_avg_accuracy = round(accuracy_score(sarcasms_labels, sarcasms_preds, sample_weight=masks) * 100, 2)
    sarcasms_avg_fscore = round(f1_score(sarcasms_labels, sarcasms_preds, sample_weight=masks,average='weighted') * 100, 2)

    avg_loss=round(sarcasms_avg_loss, 2)
    avg_accuracy=round(sarcasms_avg_accuracy, 2)
    avg_fscore=sarcasms_avg_fscore
    return avg_loss, avg_accuracy, sarcasms_labels, sarcasms_preds, implicit_labels,implicit_preds,explicit_labels,explicit_preds,\
           masks, avg_fscore, [alphas, alphas_f, alphas_b, vids]


if __name__ == '__main__':

    main_start_time = time.time()
    seed_torch(12345678)
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-cuda', action='store_true', default=False,
                        help='does not use GPU')
    parser.add_argument('--lr', type=float, default=0.0001, metavar='LR',
                        help='learning rate')
    parser.add_argument('--l2', type=float, default=0.00001, metavar='L2',
                        help='L2 regularization weight')
    parser.add_argument('--rec-dropout', type=float, default=0.5,
                        metavar='rec_dropout', help='rec_dropout rate')
    parser.add_argument('--dropout', type=float, default=0.5, metavar='dropout',
                        help='dropout rate')
    parser.add_argument('--batch-size', type=int, default=64, metavar='BS',
                        help='batch size')
    parser.add_argument('--epochs', type=int, default=100, metavar='E',
                        help='number of epochs')
    parser.add_argument('--class-weight', action='store_true', default=False,
                        help='class weight')
    parser.add_argument('--active-listener', action='store_true', default=False,
                        help='active listener')
    parser.add_argument('--attention', default='general', help='Attention type')
    args = parser.parse_args()

    print(args)

    args.cuda = torch.cuda.is_available() and not args.no_cuda
    if args.cuda:
        print('Running on GPU')
    else:
        print('Running on CPU')

    batch_size = args.batch_size
    cuda = args.cuda
    n_epochs = args.epochs

    D_m_T = 1744
    D_m_V = 1744
    D_m_A = 1744
    D_m = 100
    D_g = 150
    D_p = 150
    D_e = 100
    D_h = 100
    D_a = 100

    sarcasm_n_classes_out = 2
    implicit_n_classes_out = 3
    explicit_n_classes_out = 3

    sarcasm_loss_function = MaskedNLLLoss()
    implicit_loss_function = MaskedNLLLoss()
    explicit_loss_function = MaskedNLLLoss()
    all_data_path = ['data/MUSTARD_data/MUSTARD_combin_feature.pkl']

    Kfold = len(all_data_path)
    time1 = time.time()

    test_0_precision = 0
    test_0_recall = 0
    test_0_f1score = 0

    test_1_precision = 0
    test_1_recall = 0
    test_1_f1score = 0

    test_accuracy = 0

    test_macro_precision = 0
    test_macro_recall = 0
    test_macro_f1score = 0

    test_weight_precision = 0
    test_weight_recall = 0
    test_weight_f1score = 0
    k = 0
    model = MUSTARDBiModelTri_test(D_m_T, D_m_V, D_m_A, D_m, D_g, D_p, D_e, D_h,
                                    sarcasm_n_classes=sarcasm_n_classes_out,
                                    implicit_n_classes=implicit_n_classes_out,
                                    explicit_n_classes=explicit_n_classes_out,
                                    listener_state=args.active_listener,
                                    context_attention=args.attention,
                                    dropout_rec=args.rec_dropout,
                                    dropout=args.dropout
                                    )
    if cuda:
        model.cuda()
    data_path = all_data_path[k]
    optimizer = optim.Adam(model.parameters(), lr=args.lr, weight_decay=args.l2)
    train_loader, valid_loader, test_loader = get_MUSTARD_loaders(data_path, valid=0.3, batch_size=batch_size, num_workers=2)

    best_loss, best_sarcasms_label, best_sarcasms_pred, best_implicit_label, best_implicit_pred, best_explicit_label, best_explicit_pred, best_mask, best_fscore \
        = None, None, None, None, None, None, None, None, None
    for e in range(args.epochs):
        start_time = time.time()
        train_loss, train_acc, _, _, _, _, _, _,_, train_fscore, _ = train_or_eval_model(model, sarcasm_loss_function,implicit_loss_function,
                                                                                explicit_loss_function,train_loader, e, optimizer, True)

        valid_loss, valid_acc, _, _, _, _, _, _,_, val_fscore, _ = train_or_eval_model(model, sarcasm_loss_function,implicit_loss_function,
                                                                                        explicit_loss_function, valid_loader, e)

        test_loss, test_acc, sarcasms_label, sarcasms_pred, implicit_label,implicit_pred,explicit_label,explicit_pred,\
        test_mask, test_fscore, attentions = train_or_eval_model(model,sarcasm_loss_function,implicit_loss_function,explicit_loss_function,test_loader,e )

        if best_fscore == None or val_fscore > best_fscore:
            best_loss, best_sarcasms_label, best_sarcasms_pred, best_implicit_label,best_implicit_pred,best_explicit_label,best_explicit_pred,best_mask, best_attn = \
            test_loss, sarcasms_label, sarcasms_pred,implicit_label,implicit_pred,explicit_label,explicit_pred, test_mask, attentions
            best_fscore = val_fscore
            best_epoch = e + 1
    new_sarcasms_f1score= round(f1_score(best_sarcasms_label, best_sarcasms_pred,  sample_weight=best_mask, average='weighted') * 100, 4)
    result = classification_report(best_sarcasms_label, best_sarcasms_pred, sample_weight=best_mask, digits=10, output_dict=True)