from typing import List, Union

import nlopt as nl
import numpy as np

from allopy.optimize import BaseOptimizer
from allopy.optimize.algorithms import has_gradient, map_algorithm
from allopy.optimize.utils import create_matrix_constraint, validate_matrix_constraints
from .constraint import ConstraintMap
from .types import Arg1Func, Arg2Func

__all__ = ["ModelBuilder"]

ObjectiveFunc = Union[List[Arg1Func], List[Arg2Func]]
ConstraintFunc = Union[List[Arg1Func], List[Arg2Func]]


class ModelBuilder:
    def __init__(self,
                 num_assets: int,
                 num_scenarios: int,
                 algorithm,
                 sum_to_1: bool,
                 x_tol_abs: float,
                 x_tol_rel: float,
                 f_tol_abs: float,
                 f_tol_rel: float,
                 max_eval: float,
                 c_eps: float,
                 verbose: bool):
        algorithm = map_algorithm(algorithm) if isinstance(algorithm, str) else algorithm
        if has_gradient(algorithm) == 'NOT COMPILED':
            raise NotImplementedError(
                f"Cannot use '{nl.algorithm_name(algorithm)}' as it is not compiled")

        self.num_scenarios = num_scenarios
        self.num_assets = num_assets
        self.algorithm = algorithm
        self.max_or_min = None
        self._obj_funcs: ObjectiveFunc = []
        self.lower_bounds = np.repeat(0, num_assets)
        self.upper_bounds = np.repeat(1, num_assets)
        self.constraints = ConstraintMap(num_scenarios)

        self.x_tol_abs = x_tol_abs
        self.x_tol_rel = x_tol_rel
        self.f_tol_abs = f_tol_abs
        self.f_tol_rel = f_tol_rel
        self.max_eval = max_eval
        self.c_eps = c_eps
        self.verbose = verbose
        self.sum_to_1 = sum_to_1

    def __call__(self, index: int):
        """Creates the individual optimization model for the first step"""
        model = BaseOptimizer(self.num_assets, self.algorithm, verbose=self.verbose)
        model.set_bounds(self.lower_bounds, self.upper_bounds)

        # sets up optimizer's programs and bounds
        for item, set_option in [
            (self.x_tol_abs, model.set_xtol_abs),
            (self.x_tol_rel, model.set_xtol_rel),
            (self.f_tol_abs, model.set_ftol_abs),
            (self.f_tol_rel, model.set_ftol_rel),
            (self.max_eval, model.set_maxeval),
        ]:
            if item is not None:
                set_option(item)

        # constraint functions are applied to each model (based on index)
        # matrix constraints have been transformed to functions in RegretOptimizer,
        # thus we use add_(in)equality_constraint constraint directly
        for constraints, set_constraint in [
            (self.constraints.equality, model.add_equality_constraint),
            (self.constraints.inequality, model.add_inequality_constraint),
            (self.constraints.m_equality, model.add_equality_constraint),
            (self.constraints.m_inequality, model.add_inequality_constraint)
        ]:
            for fn_list in constraints.values():
                set_constraint(fn_list[index], self.c_eps)

        if self.sum_to_1:
            model.add_equality_constraint(lambda x: sum(x) - 1)

        # sets up the objective function
        assert self.max_or_min in ('maximize', 'minimize') and len(self._obj_funcs) == self.num_scenarios, \
            "Objective function is not set yet. Use the .set_max_objective() or .set_min_objective() methods to do so"

        if self.max_or_min == "maximize":
            model.set_max_objective(self._obj_funcs[index])
        else:
            model.set_min_objective(self._obj_funcs[index])

        return model

    @property
    def obj_funcs(self):
        return self._obj_funcs

    @obj_funcs.setter
    def obj_funcs(self, functions: ObjectiveFunc):
        self._validate_num_functions(functions)
        self._obj_funcs = functions

    def add_inequality_constraints(self, functions: ConstraintFunc):
        self._validate_num_functions(functions)
        self.constraints.add_inequality_constraints(functions)

    def add_equality_constraints(self, functions: ConstraintFunc):
        self._validate_num_functions(functions)
        self.constraints.add_equality_constraints(functions)

    def add_inequality_matrix_constraints(self, A, b):
        A, b = validate_matrix_constraints(A, b)

        for i, row, limit in zip(range(len(b)), A, b):
            fn = create_matrix_constraint(row, limit, f"A_{i}")
            self.constraints.add_matrix_inequality_constraints([fn] * self.num_scenarios)

    def add_equality_matrix_constraints(self, Aeq, beq):
        Aeq, beq = validate_matrix_constraints(Aeq, beq)

        for i, row, limit in zip(range(len(beq)), Aeq, beq):
            fn = create_matrix_constraint(row, limit, f"AEQ_{i}")
            self.constraints.add_matrix_equality_constraints([fn] * self.num_scenarios)

    def _validate_num_functions(self, funcs: List):
        error_msg = f"Number of functions do not match. Functions given: {len(funcs)}. " \
                    f"Functions expected: {self.num_scenarios}"
        assert len(funcs) == self.num_scenarios, error_msg
