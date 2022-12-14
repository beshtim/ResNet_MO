import numpy as np
import tensorrt as trt
import pycuda.driver as cuda
import pycuda.autoinit
from torchvision import transforms as T
import torch 
import threading

class TRTPredictor:

    def __init__(self, engine_path: str, output_keys):
        """
        Args:
            engine_path (str): путь к весам.
            output_keys (list): массив с категориями.
        """
        self.engine_path = engine_path
        self.transform = self.get_transform()
        self.output_keys = ['pred_' + key for key in output_keys]


        # Load TRT engine
        self.logger = trt.Logger(trt.Logger.ERROR)
        trt.init_libnvinfer_plugins(self.logger, namespace="")
        with open(self.engine_path, "rb") as f, trt.Runtime(self.logger) as runtime:
            self.engine = runtime.deserialize_cuda_engine(f.read())
        self.context = self.engine.create_execution_context()
        assert self.engine
        assert self.context

        # Setup I/O bindings
        self.inputs = []
        self.outputs = []
        self.allocations = []
        for i in range(self.engine.num_bindings):
            is_input = False
            if self.engine.binding_is_input(i):
                is_input = True
            name = self.engine.get_binding_name(i)
            dtype = self.engine.get_binding_dtype(i)
            shape = self.engine.get_binding_shape(i)
            if is_input:
                self.batch_size = shape[0]
            size = np.dtype(trt.nptype(dtype)).itemsize
            for s in shape:
                size *= s
            allocation = cuda.mem_alloc(size)
            binding = {
                'index': i,
                'name': name,
                'dtype': np.dtype(trt.nptype(dtype)),
                'shape': list(shape),
                'allocation': allocation,
            }
            self.allocations.append(allocation)
            if self.engine.binding_is_input(i):
                self.inputs.append(binding)
            else:
                self.outputs.append(binding)

        assert self.batch_size > 0
        assert len(self.inputs) > 0
        assert len(self.outputs) > 0
        assert len(self.allocations) > 0

    def get_transform(self):
        transform = []
        resize = T.Resize((240, 240))
        normalize = T.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))
        output = [T.ToTensor()] + transform + [resize, normalize]    
        return T.Compose(output)
    
    def input_spec(self):
        """Получение спецификаций для входного тензора сети.

        Returns:
            _type_: Два элемента, форма входного тензора и его (numpy) тип данных.
        """
        return self.inputs[0]['shape'], self.inputs[0]['dtype']

    def output_spec(self) -> dict:
        """Получение спецификаций для выходных тензоров сети.

        Returns:
            dict: Список с двумя элементами на каждый выход: форма и (numpy) тип данных.
        """
        specs = []
        for o in self.outputs:
            specs.append((o['shape'], o['dtype']))
        return specs

    def __call__(self, img):
        """Обработка картинки

        Args:
            image (np.ndarray): Фотография
        """

        image_cut = img.numpy()
     
        # Prepare the output data
        outputs = []
        for shape, dtype in self.output_spec():
            outputs.append(np.zeros(shape, dtype))

        # Process I/O and execute the network
        cuda.memcpy_htod(self.inputs[0]['allocation'], np.ascontiguousarray(image_cut))
        self.context.execute_v2(self.allocations)
        for o in range(len(outputs)):
            cuda.memcpy_dtoh(outputs[o], self.outputs[o]['allocation'])

        # Process the results
        output_dict = {}
        for i in range(len(outputs)):
            if outputs[i].shape[1] == 1:
                pred = (torch.sigmoid(torch.tensor(outputs[i])) > 0.5).reshape(-1).numpy()
            else:
                pred = torch.max(torch.tensor(outputs[i]).data, 1)[1].numpy()
            output_dict[self.output_keys[i]] = pred
        return output_dict
