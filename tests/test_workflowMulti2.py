import oryxflow
import pandas as pd
import pytest


class Task1(oryxflow.tasks.TaskCache):
    param1 = oryxflow.Parameter()

    def run(self):
        df = pd.DataFrame({'a': range(10)})
        df['param1'] = self.param1
        self.save(df)

class Task2(oryxflow.tasks.TaskCache):
    param1 = oryxflow.Parameter()
    param2 = oryxflow.Parameter()

    def run(self):
        df = pd.DataFrame({'a': range(10)})
        df['param1'] = self.param1
        df['param2'] = self.param2
        self.save(df)


def test_workflow_multi():
    # Generate parameters
    params_values = ['a', 'b']
    params = dict()
    params_all = oryxflow.utils.params_generator_single({'param1': params_values}, params)

    # Create and run workflow
    flow = oryxflow.WorkflowMulti(Task1, params=params_all)
    flow.run()

    # Load and verify results
    results = flow.outputLoad()
    
    # Check that we have results for both parameter values
    assert len(results) == 2
    
    # Check that each result has the correct parameter value
    for idx, dfr in results.items():
        assert dfr['param1'].values[0] == params_values[idx]

    # Test with multiple parameters using params_generator_dictlist
    params_values1 = ['a', 'b']
    params_values2 = ['c', 'd']
    params = dict()
    params_all2 = oryxflow.utils.params_generator_dictlist({
        'param1': params_values1,
        'param2': params_values2
    }, params)

    # Create and run workflow with multiple parameters
    flow2 = oryxflow.WorkflowMulti(Task2, params=params_all2)
    flow2.run()

    # Load and verify results
    results2 = flow2.outputLoad()
    
    # Check that we have results for all parameter combinations
    assert len(results2) == 4  # 2 * 2 combinations
    
    # Check that each result has the correct parameter values
    expected_combinations = [
        {'param1': 'a', 'param2': 'c'},
        {'param1': 'b', 'param2': 'c'},
        {'param1': 'a', 'param2': 'd'},
        {'param1': 'b', 'param2': 'd'}
    ]
    
    for i, expected in enumerate(expected_combinations):
        dfr = results2[i]
        assert dfr['param1'].values[0] == expected['param1']
        assert dfr['param2'].values[0] == expected['param2']


def test_params_generator():
    # Test params_generator_single
    params_values = ['a', 'b']
    params_base = {'base_param': 'value'}
    
    # Test without base params
    result_single = oryxflow.utils.params_generator_single({'param1': params_values})
    assert len(result_single) == len(params_values)
    for i, value in enumerate(params_values):
        assert result_single[i] == {'param1': value}
    
    # Test with base params
    result_single_base = oryxflow.utils.params_generator_single({'param1': params_values}, params_base)
    assert len(result_single_base) == len(params_values)
    for i, value in enumerate(params_values):
        assert result_single_base[i] == {'param1': value, **params_base}
    
    # Test params_generator_dictlist with 2 parameters
    params_values1 = ['a', 'b']
    params_values2 = ['c', 'd']
    params_dict2 = {
        'param1': params_values1,
        'param2': params_values2
    }
    
    # Test without base params
    result_dictlist2 = oryxflow.utils.params_generator_dictlist(params_dict2)
    expected_combinations2 = [
        {'param1': 'a', 'param2': 'c'},
        {'param1': 'b', 'param2': 'c'},
        {'param1': 'a', 'param2': 'd'},
        {'param1': 'b', 'param2': 'd'}
    ]
    assert len(result_dictlist2) == len(expected_combinations2)
    for i, expected in enumerate(expected_combinations2):
        assert result_dictlist2[i] == expected
    
    # Test with base params
    result_dictlist2_base = oryxflow.utils.params_generator_dictlist(params_dict2, params_base)
    assert len(result_dictlist2_base) == len(expected_combinations2)
    for i, expected in enumerate(expected_combinations2):
        assert result_dictlist2_base[i] == {**expected, **params_base}
    
    # Test params_generator_dictlist with 3 parameters
    params_values3 = ['e', 'f']
    params_dict3 = {
        'param1': params_values1,
        'param2': params_values2,
        'param3': params_values3
    }
    
    # Test without base params
    result_dictlist3 = oryxflow.utils.params_generator_dictlist(params_dict3)
    expected_combinations3 = [
        {'param1': 'a', 'param2': 'c', 'param3': 'e'},
        {'param1': 'b', 'param2': 'c', 'param3': 'e'},
        {'param1': 'a', 'param2': 'd', 'param3': 'e'},
        {'param1': 'b', 'param2': 'd', 'param3': 'e'},
        {'param1': 'a', 'param2': 'c', 'param3': 'f'},
        {'param1': 'b', 'param2': 'c', 'param3': 'f'},
        {'param1': 'a', 'param2': 'd', 'param3': 'f'},
        {'param1': 'b', 'param2': 'd', 'param3': 'f'}
    ]
    assert len(result_dictlist3) == len(expected_combinations3)
    for i, expected in enumerate(expected_combinations3):
        assert result_dictlist3[i] == expected
    
    # Test with base params
    result_dictlist3_base = oryxflow.utils.params_generator_dictlist(params_dict3, params_base)
    assert len(result_dictlist3_base) == len(expected_combinations3)
    for i, expected in enumerate(expected_combinations3):
        assert result_dictlist3_base[i] == {**expected, **params_base}



