#!/bin/bash

export CUDA_DEVICE_MAX_CONNECTIONS=1

CHECKPOINT_PATH=./tmp
VOCAB_FILE=$HOME/.cache/my_huggingface_datasets/bert-base-uncased-vocab.txt
DATA_PATH="$HOME/.cache/my_huggingface_datasets/meg-bert_text_document"

BERT_ARGS="
    --bert-no-binary-head \
    --num-layers 24 \
    --hidden-size 1024 \
    --num-attention-heads 16 \
    --seq-length 512 \
    --max-position-embeddings 512 \
    --micro-batch-size 4 \
    --global-batch-size 8 \
    --lr 0.0001 \
    --train-iters 1000000 \
    --lr-decay-iters 990000 \
    --lr-decay-style linear \
    --min-lr 0.00001 \
    --weight-decay 1e-2 \
    --lr-warmup-fraction .01 \
    --clip-grad 1.0 \
    --fp16
"

DATA_ARGS="
    --data-path $DATA_PATH \
    --vocab-file $VOCAB_FILE \
    --data-impl mmap \
    --split 949,50,1
"

OUTPUT_ARGS="
    --log-interval 100 \
    --save-interval 10000 \
    --eval-interval 1000 \
    --eval-iters 10
"

torchrun ../pretrain_bert.py \
    $BERT_ARGS \
    $DATA_ARGS \
    $OUTPUT_ARGS \
    --save $CHECKPOINT_PATH \
    --load $CHECKPOINT_PATH
