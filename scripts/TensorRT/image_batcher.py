import os
import sys
import numpy as np
from PIL import Image
from torchvision import transforms as T
import pandas as pd
import json

class JsonParser:
    def __init__(self, jsons_path):
        self.jsons_path = jsons_path

    def read_json(self, image_name):
        json_path = os.path.join(self.jsons_path, image_name[:-4] + ".json")
        if os.path.exists(json_path):
            with open(json_path, 'r') as file:
                data = json.load(file)
            if len(data) == 0:
                return None
            return data
        return None

class ImageBatcher:
    """
    Creates batches of pre-processed images.
    """

    def __init__(self, input, shape, dtype, max_num_images=None, exact_batches=False):
        """
        :param input: The input directory to read images from.
        :param shape: The tensor shape of the batch to prepare, either in NCHW or NHWC format.
        :param dtype: The (numpy) datatype to cast the batched data to.
        :param max_num_images: The maximum number of images to read from the directory.
        :param exact_batches: This defines how to handle a number of images that is not an exact multiple of the batch
        size. If false, it will pad the final batch with zeros to reach the batch size. If true, it will *remove* the
        last few images in excess of a batch size multiple, to guarantee batches are exact (useful for calibration).
        :param preprocessor: Set the preprocessor to use, V1 or V2, depending on which network is being used.
        """
        self.json_parser = JsonParser("/defectdetector/data/jsons")
        self.transform = self.get_transform()
        # Find images in the given input path
        input = os.path.realpath(input)
        self.images = []

        extensions = [".jpg", ".jpeg", ".png", ".bmp"]

        def is_image(path):
            return os.path.isfile(path) and os.path.splitext(path)[1].lower() in extensions

        if os.path.isdir(input):
            self.images = [os.path.join(input, f) for f in os.listdir(input) if is_image(os.path.join(input, f))]
            self.images.sort()
        elif os.path.isfile(input):
            if is_image(input):
                self.images.append(input)
        self.num_images = len(self.images)
        if self.num_images < 1:
            print("No valid {} images found in {}".format("/".join(extensions), input))
            sys.exit(1)

        # Handle Tensor Shape
        self.dtype = dtype
        self.shape = shape
        assert len(self.shape) == 4
        self.batch_size = shape[0]
        assert self.batch_size > 0
        self.format = None
        self.width = -1
        self.height = -1
        if self.shape[1] == 3:
            self.format = "NCHW"
            self.height = self.shape[2]
            self.width = self.shape[3]
        elif self.shape[3] == 3:
            self.format = "NHWC"
            self.height = self.shape[1]
            self.width = self.shape[2]
        assert all([self.format, self.width > 0, self.height > 0])

        # Adapt the number of images as needed
        if max_num_images and 0 < max_num_images < len(self.images):
            self.num_images = max_num_images
        if exact_batches:
            self.num_images = self.batch_size * (self.num_images // self.batch_size)
        if self.num_images < 1:
            print("Not enough images to create batches")
            sys.exit(1)
        self.images = self.images[0:self.num_images]

        # Subdivide the list of images into batches
        self.num_batches = 1 + int((self.num_images - 1) / self.batch_size)
        self.batches = []
        for i in range(self.num_batches):
            start = i * self.batch_size
            end = min(start + self.batch_size, self.num_images)
            self.batches.append(self.images[start:end])

        # Indices
        self.image_index = 0
        self.batch_index = 0

        # self.preprocessor = preprocessor

    def get_transform(self):
        transform = []
        resize = T.Resize((120, 120))
        normalize = T.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))
        output = [T.ToTensor()] + transform + [resize, normalize]    
        return T.Compose(output)

    def preprocess_image(self, image_path):
        """
        The image preprocessor loads an image from disk and prepares it as needed for batching. This includes cropping,
        resizing, normalization, data type casting, and transposing.
        This Image Batcher implements two algorithms:
        * V2: The algorithm for EfficientNet V2, as defined in automl/efficientnetv2/preprocessing.py.
        * V1: The algorithm for EfficientNet V1, aka "Legacy", as defined in automl/efficientnetv2/preprocess_legacy.py.
        :param image_path: The path to the image on disk to load.
        :return: A numpy array holding the image sample, ready to be contacatenated into the rest of the batch.
        """

        image_json = self.json_parser.read_json(os.path.basename(image_path))
        img = Image.open(image_path).convert('RGB')

        if image_json is not None:            
            bbox = image_json["bbox"][0]
            x1, y1, x2, y2 = int(bbox[0]), int(bbox[1]), int(bbox[0] + bbox[2]), int(bbox[1] + bbox[3])
            img = img.crop((x1, y1, x2, y2))
            
        img = self.transform(img)
        img = img.numpy()
        return img, None

    def get_batch(self):
        """
        Retrieve the batches. This is a generator object, so you can use it within a loop as:
        for batch, images in batcher.get_batch():
           ...
        Or outside of a batch with the next() function.
        :return: A generator yielding two items per iteration: a numpy array holding a batch of images, and the list of
        paths to the images loaded within this batch.
        """
        for i, batch_images in enumerate(self.batches):
            batch_data = np.zeros(self.shape, dtype=self.dtype)
            batch_scales = [None] * len(batch_images)
            for i, image in enumerate(batch_images):
                self.image_index += 1
                batch_data[i], batch_scales[i] = self.preprocess_image(image)
            self.batch_index += 1
            yield batch_data, batch_images, batch_scales
