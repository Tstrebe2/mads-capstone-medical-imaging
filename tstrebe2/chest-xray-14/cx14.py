import pytorch_lightning as pl
import torch
import torchvision
import torchmetrics

# define the LightningModule which is similar to torch.nn.Module
class Densenet121(pl.LightningModule):
    def __init__(self,
                 learning_rate:float=1e-3, 
                 momentum:float=.9, 
                 weight_decay:float=1e-4, 
                 class_weights=None, 
                 freeze_features:str='False',
                 T_max:float=10,
                 eta_min:float=5e-5,
                 input_channels:int=1, 
                 out_features:int=1):

        super().__init__()
        self.automatic_optimization = True
        self.freeze_features = freeze_features
        self.avg_precision_score = torchmetrics.AveragePrecision(num_classes=out_features, average=None)
        # This line saves the hyper_parameters so they can be called using self.hparams...
        self.save_hyperparameters('learning_rate', 
                                  'momentum', 
                                  'weight_decay', 
                                  'class_weights',
                                  'freeze_features',
                                  'T_max',
                                  'eta_min',)

        densenet = torchvision.models.densenet121(weights='DEFAULT')
        
        self.features = densenet.features
        self.features.conv0 = torch.nn.Conv2d(input_channels, 64, kernel_size=(7, 7), stride=(2, 2), padding=(3, 3), bias=False)
        self.classifier = torch.nn.Linear(in_features=densenet.classifier.in_features, 
                                          out_features=out_features, 
                                          bias=True)
        
    def forward(self, x):
        features = self.features(x)
        out = torch.nn.functional.relu(features, inplace=True)
        out = torch.nn.functional.adaptive_avg_pool2d(out, (1, 1))
        out = torch.flatten(out, 1)
        out = self.classifier(out)
        return out
        
    def training_step(self, batch, batch_idx):
        inputs, targets = batch
        outputs = self(inputs)
        
        class_weights = self.hparams.class_weights.to(self.device)
        train_loss = torch.nn.functional.binary_cross_entropy_with_logits(outputs, targets, weight=class_weights)
        
        self.log("train_loss", train_loss, on_step=True, on_epoch=True, prog_bar=True, sync_dist=True)
        return train_loss
    
    def validation_step(self, batch, batch_idx):
        inputs, targets = batch
        outputs = self(inputs)
        
        class_weights = self.hparams.class_weights.to(self.device)
        val_loss = torch.nn.functional.binary_cross_entropy_with_logits(outputs, targets, weight=class_weights)
        
        preds = torch.sigmoid(outputs)
        avg_precision_score = self.avg_precision_score(preds, targets)
        
        self.log_dict({ "val_loss":val_loss, 
                        "val_avg_prec":avg_precision_score }, on_step=False, on_epoch=True, prog_bar=True, sync_dist=True)
    
    def test_step(self, batch, batch_idx):
        inputs, targets = batch
        outputs = self(inputs)
        
        class_weights = self.hparams.class_weights.to(self.device)
        test_loss = torch.nn.functional.binary_cross_entropy_with_logits(outputs, targets.squeeze(), weight=class_weights)
        
        self.log("test_loss", on_step=False, on_epoch=True, prog_bar=True, sync_dist=True)

    def configure_optimizers(self): 
        params_to_optimize = None
        # We must store boolean values as strings. Pytorch Lightning has a
        # bug that changes boolean values when using DDP.
        if self.hparams.freeze_features == 'True':
            for param in self.features.parameters():
                param.requires_grad=False
                
            params_to_optimize = self.classifier.parameters()
        else:
            for param in self.parameters():
                if not param.requires_grad:
                    param.requires_grad=True
                    
            params_to_optimize = self.parameters()
            
        optimizer = torch.optim.SGD(params_to_optimize, 
                                    lr=self.hparams.learning_rate, 
                                    momentum=self.hparams.momentum, 
                                    weight_decay=self.hparams.weight_decay)
        
        lr_scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer,
                                                                  T_max=self.hparams.T_max,
                                                                  eta_min=self.hparams.eta_min,
                                                                  verbose=True)
        
        return { "optimizer": optimizer,
                 "lr_scheduler": 
                   { "scheduler": lr_scheduler,
                     "interval":"epoch",
                     "frequency": 1 }, 
                }
