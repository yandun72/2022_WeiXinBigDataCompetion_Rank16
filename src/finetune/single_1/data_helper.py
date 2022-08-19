import json
import random
import zipfile
from io import BytesIO
from functools import partial
from collections import OrderedDict
import numpy as np
from torchvision.transforms import Compose, Resize, CenterCrop, Normalize, ToTensor
import os
from io import BytesIO
import torch
from torch.utils.data import DataLoader, Dataset, RandomSampler, SequentialSampler
from transformers import BertTokenizer
from PIL import Image
from category_id_map import category_id_to_lv2id,CATEGORY_ID_LIST
tmp_dict = OrderedDict()
for s in CATEGORY_ID_LIST:
    ss = s[0:2]
    if ss not in tmp_dict:
        tmp_dict[ss] = 1
    else:
        tmp_dict[ss] += 1
    
list2 = [0]
for key in tmp_dict:
    list2.append(list2[-1] + tmp_dict[key])


def create_dataloaders(args):
    dataset = MultiModalDataset(args, args.train_annotation, args.train_zip_feats)
    size = len(dataset)
    val_size = int(size * args.val_ratio)
    train_dataset, val_dataset = torch.utils.data.random_split(dataset, [size - val_size, val_size],
                                                               generator=torch.Generator().manual_seed(args.seed))

    if args.num_workers > 0:
        dataloader_class = partial(DataLoader, pin_memory=True, num_workers=args.num_workers)
    else:
        # single-thread reading does not support prefetch_factor arg
        dataloader_class = partial(DataLoader, pin_memory=True, num_workers=0)

    train_sampler = RandomSampler(train_dataset)
    val_sampler = SequentialSampler(val_dataset)
    train_dataloader = dataloader_class(train_dataset,
                                        batch_size=args.batch_size,
                                        sampler=train_sampler,
                                        drop_last=True)
    val_dataloader = dataloader_class(val_dataset,
                                      batch_size=args.val_batch_size,
                                      sampler=val_sampler,
                                      drop_last=False)
    return train_dataloader, val_dataloader


class MultiModalDataset(Dataset):
    """ A simple class that supports multi-modal inputs.
    For the visual features, this dataset class will read the pre-extracted
    features from the .npy files. For the title information, it
    uses the BERT tokenizer to tokenize. We simply ignore the ASR & OCR text in this implementation.
    Args:
        ann_path (str): annotation file path, with the '.json' suffix.
        zip_feats (str): visual feature zip file path.
        test_mode (bool): if it's for testing.
    """

    def __init__(self,
                 args,
                 zip_feat_path,
                 data,
                 test_mode: bool = False):
        self.args = args
        
        
            
        self.zip_feat_path = zip_feat_path
        if test_mode == False:
            self.max_frame = args.max_frames
            self.bert_seq_length = args.bert_seq_length
        else:
            self.max_frame = args.max_frames_infer
            self.bert_seq_length = args.bert_seq_length_infer
        self.test_mode = test_mode
        self.num_workers = args.num_workers
             
        self.data = data
        self.raw_data_len = 10000
        self.id = data.id.to_list()
        self.title = data.title.to_list()
        self.asr = data.asr.to_list()
        self.raw_index = data.raw_index.to_list()
        self.ocr = data.ocr.to_list()
        self.tokenizer = BertTokenizer.from_pretrained(args.bert_dir, use_fast=True,never_split = ['[unused1]','[unused2]','[unused3]','[unused4]'])
        if args.vision_model != 'swin_v2':
            self.transform = Compose([
                Resize(256),
                CenterCrop(224),
                ToTensor(),
                Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            ])
        else:
            self.transform = Compose([
                Resize(256),
                CenterCrop(192),
                ToTensor(),
                Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            ])  
        if self.num_workers > 0:
            # lazy initialization for zip_handler to avoid multiprocessing-reading error
            self.handles_1 = [None for _ in range(args.num_workers)]
            self.handles_0 = [None for _ in range(args.num_workers)]
        else:
            self.handles_1 = zipfile.ZipFile(self.zip_feat_path+'/labeled_1.zip', 'r')
            self.handles_0 = zipfile.ZipFile(self.zip_feat_path+'/labeled_0.zip', 'r')
    def __len__(self) -> int:
        return len(self.data)

    def get_visual_frames(self, idx: int) -> tuple:
        vid = self.id[idx]
        raw_index = self.raw_index[idx]


        #print(vid,idx,raw_index,self.data_len,zip_path)
        if self.num_workers > 0:
            worker_id = torch.utils.data.get_worker_info().id
            if self.handles_0[worker_id] is None:
                self.handles_0[worker_id] = zipfile.ZipFile(self.zip_feat_path + '/labeled_0.zip', 'r')
            if self.handles_1[worker_id] is None:
                self.handles_1[worker_id] = zipfile.ZipFile(self.zip_feat_path + '/labeled_1.zip', 'r')
            handle_0 = self.handles_0[worker_id]
            handles_1 = self.handles_1[worker_id]
        else:
            handle_1 = self.handles_1
            handle_0 = self.handles_0
            
        try:
            raw_feats = np.load(BytesIO(handle_0.read(name=f'{vid}.npy')), allow_pickle=True)
        except Exception as e:
            raw_feats = np.load(BytesIO(handles_1.read(name=f'{vid}.npy')), allow_pickle=True)
        raw_feats = raw_feats.astype(np.float32)  # float16 to float32
        num_frames, feat_dim = raw_feats.shape
        feat = np.zeros((self.max_frame, feat_dim), dtype=np.float32)
        mask = np.ones((self.max_frame,), dtype=np.int32)

        if num_frames <= self.max_frame:
            feat[:num_frames] = raw_feats
            mask[num_frames:] = 0
            
        else:
            # if the number of frames exceeds the limitation, we need to sample
            # the frames.
            if self.test_mode:
                # uniformly sample when test mode is True
                step = num_frames // self.max_frame
                select_inds = list(range(0, num_frames, step))
                select_inds = select_inds[:self.max_frame]
            else:
                # randomly sample when test mode is False
                select_inds = list(range(num_frames))
                random.shuffle(select_inds)
                select_inds = select_inds[:self.max_frame]
                select_inds = sorted(select_inds)
            for i, j in enumerate(select_inds):
                feat[i] = raw_feats[j]
        feat = torch.from_numpy(feat)
        mask = torch.LongTensor(mask)
        return feat, mask
    

    
    def tokenize_text(self, text: str) -> tuple:#文本
        encoded_inputs = self.tokenizer(text, max_length=self.bert_seq_length, padding='max_length', truncation=True)
        input_ids = torch.LongTensor(encoded_inputs['input_ids'])
        mask = torch.LongTensor(encoded_inputs['attention_mask'])
        return input_ids, mask

    def __getitem__(self, idx: int) -> dict:
        # Step 1, load visual features from zipfile.
        frame_input, frame_mask = self.get_visual_frames(idx)

        # Step 2, load title tokens
        ocr_text = []
        for o in self.ocr[idx]:
            ocr_text.append(o['text'])
        ocr_text=''.join(ocr_text)

        #todo
        all_text = '[unused4]'+self.title[idx] +'[unused1]'+ self.asr[idx] + '[unused3]' + ocr_text
        title_input, title_mask = self.tokenize_text(all_text)
        text_token_type = torch.LongTensor([0]*len(title_input))
        
        video_token_type = torch.LongTensor([1]*len(frame_input))
        # Step 3, summarize into a dictionary
        data = dict(
            frame_input=frame_input,
            frame_mask=frame_mask,
            title_input=title_input,
            title_mask=title_mask,
            text_token_type=text_token_type,
            video_token_type=video_token_type
        )
        if not self.test_mode:
            label = category_id_to_lv2id(self.data.category_id.to_list()[idx])
            data['label'] = torch.LongTensor([label])
        return data
    
