# Copyright 2019 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# ShuffleNet v1.0
# Paper: https://arxiv.org/pdf/1707.01083.pdf

import tensorflow as tf
from tensorflow.keras import Input, Model
from tensorflow.keras.layers import Dense, Conv2D, BatchNormalization, ReLU, MaxPooling2D, GlobalAveragePooling2D
from tensorflow.keras.layers import Add, Concatenate, AveragePooling2D, DepthwiseConv2D, Lambda
from tensorflow.keras import backend as K

def stem(inputs):
    ''' Stem Convolution Group 
        inputs : input image (tensor)
    '''
    x = Conv2D(24, (3, 3), strides=2, padding='same', use_bias=False)(inputs)
    x = BatchNormalization()(x)
    x = ReLU()(x)
    x = MaxPooling2D((3, 3), strides=2, padding='same')(x)
    return x
    
def classifier(x, n_classes):
    ''' Classifier Group 
        x : input tensor
        n_classes : number of output classes
    '''
    # Use global average pooling to flatten feature maps to 1D vector, where
    # each feature map is a single averaged value (pixel) in flatten vector
    x = GlobalAveragePooling2D()(x)
    x = Dense(n_classes, activation='softmax')(x)
    return x
    
def shuffle_group(x, n_groups, n_blocks, n_filters, reduction):
    ''' A Shuffle Group 
        x        : input tensor
        n_groups : number of groups to partition feature maps (channels) into.
        n_blocks : number of shuffle blocks for this group
        n_filters: number of output filters
        reduction: dimensionality reduction
    '''
    
    # first block is a strided shuffle block
    x = strided_shuffle_block(x, n_groups, n_filters, reduction)
    
    # remaining shuffle blocks in group
    for _ in range(n_blocks-1):
        x = shuffle_block(x, n_groups, n_filters, reduction)
    return x
    
def strided_shuffle_block(x, n_groups, n_filters, reduction):
    ''' A Strided Shuffle Block 
        x           : input tensor
        n_groups    : number of groups to partition feature maps (channels) into.
        n_filters   : number of filters
        reduction   : dimensionality reduction factor (e.g, 0.25)
    '''
    # projection shortcut
    shortcut = x
    shortcut = AveragePooling2D((3, 3), strides=2, padding='same')(shortcut)   
    
    # On entry block, we need to adjust the number of output filters
    # of the entry pointwise group convolution to match the exit
    # pointwise group convolution, by subtracting the number of input filters
    n_filters -= int(x.shape[3])
    
    # pointwise group convolution, with dimensionality reduction
    x = pw_group_conv(x, n_groups, int(reduction * n_filters))
    x = ReLU()(x)
    
    # channel shuffle layer
    x = channel_shuffle(x, n_groups)

    # Depthwise 3x3 Strided Convolution
    x = DepthwiseConv2D((3, 3), strides=2, padding='same', use_bias=False)(x)
    x = BatchNormalization()(x)

    # pointwise group convolution, with dimensionality restoration
    x = pw_group_conv(x, n_groups, n_filters)
    
    # Concatenate the projection shortcut to the output
    x = Concatenate()([shortcut, x])
    x = ReLU()(x)
    return x
    
def shuffle_block(x, n_groups, n_filters, reduction):
    ''' A Shuffle block  
        x        : input tensor
        n_groups : number of groups to partition feature maps (channels) into.
        n_filters   : number of filters
        reduction   : dimensionality reduction factor (e.g, 0.25)
    '''
    # identity shortcut
    shortcut = x
    
    # pointwise group convolution, with dimensionality reduction
    x = pw_group_conv(x, n_groups, int(reduction * n_filters))
    x = ReLU()(x)
    
    # channel shuffle layer
    x = channel_shuffle(x, n_groups)
    
    # Depthwise 3x3 Convolution
    x = DepthwiseConv2D((3, 3), strides=1, padding='same', use_bias=False)(x)
    x = BatchNormalization()(x)
    
    # pointwise group convolution, with dimensionality restoration
    x = pw_group_conv(x, n_groups, n_filters)
    
    # Add the identity shortcut (input added to output)
    x = Add()([shortcut, x])
    x = ReLU()(x)
    return x

def pw_group_conv(x, n_groups, n_filters):
    ''' A Pointwise Group Convolution  
        x        : input tensor
        n_groups : number of groups to partition feature maps (channels) into.
        n_filers : number of filters
    '''
    # Calculate the number of input filters (channels)
    in_filters = x.shape[3]

    # Derive the number of input filters (channels) per group
    grp_in_filters  = in_filters // n_groups
    # Derive the number of output filters per group (Note the rounding up)
    grp_out_filters = int(n_filters / n_groups + 0.5)
      
    # Perform convolution across each channel group
    groups = []
    for i in range(n_groups):
        # Slice the input across channel group
        group = Lambda(lambda x: x[:, :, :, grp_in_filters * i: grp_in_filters * (i + 1)])(x)

        # Perform convolution on channel group
        conv = Conv2D(grp_out_filters, (1,1), padding='same', strides=1, use_bias=False)(group)
        # Maintain the point-wise group convolutions in a list
        groups.append(conv)

    # Concatenate the outputs of the group pointwise convolutions together
    x = Concatenate()(groups)
    # Do batch normalization of the concatenated filters (feature maps)
    x = BatchNormalization()(x)
    return x
    
def channel_shuffle(x, n_groups):
    ''' Implements the channel shuffle layer 
        x        : input tensor
        n_groups : number of groups to partition feature maps (channels) into.
    '''
    # Get dimensions of the input tensor
    batch, height, width, n_filters = x.shape

    # Derive the number of input filters (channels) per group
    grp_in_filters  = n_filters // n_groups

    # Separate out the channel groups
    x = Lambda(lambda z: K.reshape(z, [-1, height, width, n_groups, grp_in_filters]))(x)
    # Transpose the order of the channel groups (i.e., 3, 4 => 4, 3)
    x = Lambda(lambda z: K.permute_dimensions(z, (0, 1, 2, 4, 3)))(x)
    # Restore shape
    x = Lambda(lambda z: K.reshape(z, [-1, height, width, n_filters]))(x)
    return x
    
# The number of groups to partition the filters (channels)
n_groups=2

# meta-parameter: number of groups to partition feature maps (key), and
# corresponding number of output filters (value)
filters = {
        1: [24, 144, 288, 576],
        2: [24, 200, 400, 800],
        3: [24, 240, 480, 960],
        4: [24, 272, 544, 1088],
        8: [24, 384, 768, 1536]
}

# meta-parameter: the dimensionality reduction on entry to a shuffle block
reduction = 0.25

# number of shuffle blocks per shuffle group
blocks = [4, 8, 4 ]

# input tensor
inputs = Input( (224, 224, 3) )

# Create the stem convolution group (referred to as Stage 1)
x = stem(inputs)

# Assemble the shuffle groups
for i in range(3):
    x = shuffle_group(x, n_groups, blocks[i], filters[n_groups][i+1], reduction)
    
# Add the final classifier layer
outputs = classifier(x, 1000)

model = Model(inputs, outputs)
model.summary()