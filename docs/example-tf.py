import oryxflow
import tensorflow as tf
from oryxflow.tasks.h5 import TaskH5Keras


# define workflow
class TaskGetTrainData(oryxflow.tasks.TaskPickle):  # save dataframe as pickle

    def run(self):
        mnist = tf.keras.datasets.mnist
        data = {}
        (data['x'], data['y']), _ = mnist.load_data()
        data['x'] = data['x'] / 255.0
        self.save(data)

class TaskGetTestData(oryxflow.tasks.TaskPickle):  # save dataframe as pickle

    def run(self):
        mnist = tf.keras.datasets.mnist
        data = {}
        _, (data['x'], data['y']) = mnist.load_data()
        data['x'] = data['x'] / 255.0
        self.save(data)

class TaskGetModel(TaskH5Keras):  # save dataframe as hdf5

    def run(self):
        model = tf.keras.models.Sequential([
            tf.keras.layers.Input(shape=(28, 28)),
            tf.keras.layers.Flatten(),
            tf.keras.layers.Dense(512, activation='relu'),
            tf.keras.layers.Dropout(0.2),
            tf.keras.layers.Dense(10, activation='softmax')
        ])
        model.compile(optimizer='adam',
                      loss='sparse_categorical_crossentropy',
                      metrics=['accuracy'])

        self.save(model)

@oryxflow.requires(TaskGetTrainData, TaskGetModel)
class TaskTrainModel(TaskH5Keras): # save output as hdf5
    epochs = oryxflow.IntParameter(default=5)

    def run(self):
        data, model = self.inputLoad()
        # or select by name with a dict requires ({'data':..., 'model':...}):
        # data = self.inputLoad(task='data')
        model.fit(data['x'], data['y'], epochs=self.epochs)
        self.save(model)

@oryxflow.requires(TaskGetTestData, TaskTrainModel)
class TaskTestModel(oryxflow.tasks.TaskPickle): # save output as pickle

    def run(self):
        data, model = self.inputLoad()
        # or select by name with a dict requires ({'data':..., 'model':...}):
        # data = self.inputLoad(task='data')
        results = model.evaluate(data['x'], data['y'])
        self.save(results)

# Check task dependencies and their execution status
flow = oryxflow.Workflow(TaskTestModel)
flow.run()
