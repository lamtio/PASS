# ERFNet full model definition for Pytorch
# Sept 2017
# Eduardo Romera
#######################

import torch
import torch.nn as nn
import torch.nn.init as init
import torch.nn.functional as F
from sppad import Conv2d as spConv2d, UpsamplingBilinear2d

class DownsamplerBlock (nn.Module):
    def __init__(self, ninput, noutput):
        super().__init__()

        #self.conv = spConv2d(ninput, noutput-ninput, kernel_size=3, stride=2, padding=1, use_sppad=True, split=4)
        self.conv = nn.Conv2d(ninput, noutput-ninput, (3, 3), stride=2, padding=1, bias=True)
        self.pool = nn.MaxPool2d(2, stride=2)
        self.bn = nn.BatchNorm2d(noutput, eps=1e-3)

    def forward(self, input):
        output = torch.cat([self.conv(input), self.pool(input)], 1)
        output = self.bn(output)
        return F.relu(output)
    
    #TODO: 1Dv28 downsampler has dropout as well

class non_bottleneck_1d (nn.Module):
    def __init__(self, chann, dropprob, dilated):        #TODO: check if 3x1 is height in Torch 
        super().__init__()

        self.conv3x1_1 = nn.Conv2d(chann, chann, (3, 1), stride=1, padding=(1,0), bias=True)

        self.conv1x3_1 = nn.Conv2d(chann, chann, (1,3), stride=1, padding=(0,1), bias=True)
        #self.conv1x3_1 = spConv2d(chann, chann, kernel_size=(1, 3),padding=(0,1), use_sppad=True, split=4)

        self.bn1 = nn.BatchNorm2d(chann, eps=1e-03)

        self.conv3x1_2 = nn.Conv2d(chann, chann, (3, 1), stride=1, padding=(1*dilated,0), bias=True, dilation = (dilated,1))

        self.conv1x3_2 = nn.Conv2d(chann, chann, (1,3), stride=1, padding=(0,1*dilated), bias=True, dilation = (1, dilated))
        #self.conv1x3_2 = spConv2d(chann, chann, (1,3), stride=1, padding=(0,dilated), dilation =  dilated, use_sppad=True, split=4)

        self.bn2 = nn.BatchNorm2d(chann, eps=1e-03)

        self.dropout = nn.Dropout2d(dropprob)
        

    def forward(self, input):

        output = self.conv3x1_1(input)
        output = F.relu(output)
        output = self.conv1x3_1(output)
        output = self.bn1(output)
        output = F.relu(output)

        output = self.conv3x1_2(output)
        output = F.relu(output)
        output = self.conv1x3_2(output)
        output = self.bn2(output)
        #output = F.relu(output)    #ESTO ESTABA MAL

        if (self.dropout.p != 0):
            output = self.dropout(output)
        
        return F.relu(output+input)    #+input = identity (residual connection)


class Encoder(nn.Module):
    def __init__(self, num_classes):
        super().__init__()
        self.initial_block = DownsamplerBlock(3,16)

        self.layers = nn.ModuleList()

        self.layers.append(DownsamplerBlock(16,64))

        for x in range(0, 5):    #5 times
           self.layers.append(non_bottleneck_1d(64, 0.00, 1))   #Dropout here was wrong in prev trainings

        self.layers.append(DownsamplerBlock(64,128))

        for x in range(0, 2):    #2 times
            self.layers.append(non_bottleneck_1d(128, 0.0, 2))
            self.layers.append(non_bottleneck_1d(128, 0.0, 4))
            self.layers.append(non_bottleneck_1d(128, 0.0, 8))
            self.layers.append(non_bottleneck_1d(128, 0.0, 16))

        #TODO: descomentar para encoder
        self.output_conv = nn.Conv2d(128, num_classes, 1, stride=1, padding=0, bias=True)

    def forward(self, input, predict=False):
        output = self.initial_block(input)

        for layer in self.layers:
            output = layer(output)

        if predict:
            output = self.output_conv(output)

        return output


class UpsamplerBlock (nn.Module):
    def __init__(self, ninput, noutput):
        super().__init__()
        self.conv = nn.ConvTranspose2d(ninput, noutput, 3, stride=2, padding=1, output_padding=1, bias=True)
        self.bn = nn.BatchNorm2d(noutput, eps=1e-3)

    def forward(self, input):
        output = self.conv(input)
        output = self.bn(output)
        return F.relu(output)

class FGlo(nn.Module):
    """
    the FGlo class is employed to refine the feature
    """
    def __init__(self, channel, reduction=16):
        super(FGlo, self).__init__()
        self.avg_pool = nn.AdaptiveAvgPool2d(1)
        self.fc = nn.Sequential(
                nn.Linear(channel, channel // reduction),
                nn.ReLU(inplace=True),
                nn.Linear(channel // reduction, channel),
                nn.Sigmoid()
        )

    def forward(self, x):
        b, c, _, _ = x.size()
        y = self.avg_pool(x).view(b, c)
        y = self.fc(y).view(b, c, 1, 1)
        return x * y

class PSPDec(nn.Module):

    def __init__(self, in_features, out_features, downsize, upsize=(64,128)):
        super(PSPDec,self).__init__()

        self.F_glo=FGlo(in_features,reduction=16)
          
        self.features = nn.Sequential(
            nn.AvgPool2d(downsize, stride=downsize),
            nn.Conv2d(in_features, out_features, 1, bias=False),
            nn.BatchNorm2d(out_features, momentum=.95),
            nn.ReLU(inplace=True),
            #nn.UpsamplingBilinear2d(upsize)
            nn.Upsample(size=upsize, mode='bilinear')
        )

    def forward(self, x):
        x = self.F_glo(x)
        return self.features(x)



class Decoder (nn.Module):
    def __init__(self, num_classes):
        super().__init__()

        #self.F_glo=FGlo(in_features,reduction=16)
        #H=480/8 240/8
        #W=640/8 320/8
        self.layer5a = PSPDec(128, 32, (64,128),(64,128))
        self.layer5b = PSPDec(128, 32, (32,64),(64,128))
        self.layer5c = PSPDec(128, 32, (16,32),(64,128))
        self.layer5d = PSPDec(128, 32, (8,16),(64,128))

        self.final = nn.Sequential(
            nn.Conv2d(256, 256, 3, padding=1, bias=False),
            nn.BatchNorm2d(256, momentum=.95),
            nn.ReLU(inplace=True),
            nn.Dropout(0.00),
            nn.Conv2d(256, num_classes, 1),
        )


    def forward(self, x):
        #x=x[0]
        
        #x = F.max_pool2d(x, (1, 4), (1, 4)) #here

        x = self.final(torch.cat([
            x,
            self.layer5a(x),
            self.layer5b(x),
            self.layer5c(x),
            self.layer5d(x),
        ], 1))

        #print('final', x.size())

        return F.upsample(x,size=(512,1024), mode='bilinear')

#ERFNet
class Net(nn.Module):
    def __init__(self, num_classes, encoder=None):  #use encoder to pass pretrained encoder
        super().__init__()

        if (encoder == None):
            self.encoder = Encoder(num_classes)
        else:
            self.encoder = encoder

        self.decoder = Decoder(num_classes)

    def forward(self, input, only_encode=False):
        if only_encode:
            return self.encoder.forward(input, predict=True)
        else:
            output = self.encoder(input)    #predict=False by default
            return self.decoder.forward(output)