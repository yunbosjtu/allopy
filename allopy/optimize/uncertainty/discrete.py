import inspect
from abc import ABC, abstractmethod
from functools import partial
from typing import Callable, List, Optional, Tuple, Union

import nlopt as nl
import numpy as np

from allopy import get_option
from allopy.types import Numeric, OptArray, OptReal
from .result import ConstraintFuncMap, ConstraintMap, Result
from ..algorithms import LD_SLSQP, has_gradient, map_algorithm
from ..utils import *

Cubes = Union[np.ndarray, List[np.ndarray], Tuple[np.ndarray]]

__all__ = ["Cubes", "DiscreteUncertaintyOptimizer"]


class DiscreteUncertaintyOptimizer(ABC):

    def __init__(self, num_assets: int, num_scenarios: int, prob: OptArray = None, algorithm=LD_SLSQP, *args, **kwargs):
        """
        The DiscreteUncertaintyOptimizer is an abstract optimizer used for optimizing under uncertainty.
        It's main optimization method must be implemented. See the :class:`RegretOptimizer` as an example.

        The default algorithm used is Sequential Least Squares Quadratic Programming.

        Parameters
        ----------
        num_assets: int
            number of assets

        num_scenarios: int
            number of scenarios

        prob
            vector containing probability of each scenario occurring

        algorithm: str
            the optimization algorithm

        args:
            other arguments to setup the optimizer

        kwargs:
            other keyword arguments
        """
        self._algorithm = map_algorithm(algorithm) if isinstance(algorithm, str) else algorithm

        assert isinstance(num_assets, int), "num_assets must be an integer"
        assert isinstance(num_scenarios, int), "num_assets must be an integer"
        self._num_assets = num_assets
        self._num_scenarios = num_scenarios
        self._prob = np.repeat(1 / num_scenarios, num_scenarios)
        self.set_prob(prob)

        self._model = nl.opt(algorithm, num_assets, *args)

        has_grad = has_gradient(self._algorithm)
        if has_grad == 'NOT COMPILED':
            raise NotImplementedError(f"Cannot use '{nl.algorithm_name(self._algorithm)}' as it is not compiled")

        # optimizer setup
        self._auto_grad: bool = kwargs.get('auto_grad', has_grad)
        self._eps: Optional[float] = kwargs.get("eps_step", get_option('EPS.STEP'))
        self._c_eps: Optional[float] = kwargs.get("c_eps", get_option('EPS.CONSTRAINT'))
        self._x_tol_abs: Optional[float] = kwargs.get("xtol_abs", get_option('EPS.X_ABS'))
        self._x_tol_rel: Optional[float] = kwargs.get("xtol_rel", None)
        self._f_tol_abs: Optional[float] = kwargs.get("ftol_abs", get_option('EPS.FUNCTION'))
        self._f_tol_rel: Optional[float] = kwargs.get("ftol_rel", None)
        self._max_eval: Optional[float] = kwargs.get("max_eval", get_option('MAX.EVAL'))
        self._stop_val: Optional[float] = kwargs.get("stop_val", None)

        # func
        self._obj_fun: List[Callable[[np.ndarray], float]] = []

        # constraint map
        self._hin: ConstraintFuncMap = {}
        self._heq: ConstraintFuncMap = {}
        self._min: ConstraintMap = {}
        self._meq: ConstraintMap = {}
        self._lb: OptArray = None
        self._ub: OptArray = None

        # result formatting options
        self._result: Optional[Result] = None
        self._max_or_min = None
        self._verbose = kwargs.get('verbose', False)

    @property
    def prob(self):
        """Vector containing probability of each scenario occurring"""
        return self._prob

    @prob.setter
    def prob(self, prob: OptArray):
        self.set_prob(prob)

    @property
    def lower_bounds(self):
        """Lower bound of each variable"""
        return np.asarray(self._model.get_lower_bounds(), np.float64)

    @lower_bounds.setter
    def lower_bounds(self, lb: Union[int, float, np.ndarray]):
        self.set_lower_bounds(lb)

    @property
    def upper_bounds(self):
        """Upper bound of each variable"""
        return np.asarray(self._model.get_upper_bounds(), np.float64)

    @upper_bounds.setter
    def upper_bounds(self, ub: Union[int, float, np.ndarray]):
        self.set_upper_bounds(ub)

    def set_max_objective(self, fn: Callable, scenarios: Cubes):
        """
        Sets the optimizer to maximize the objective function. If gradient of the objective function is not set and the
        algorithm used to optimize is gradient-based, the optimizer will attempt to insert a smart numerical gradient
        for it.

        Parameters
        ----------
        fn: Callable
            Objective function. The first argument of the function takes in the cube while the second argument
            takes in the weight.

        scenarios
            A list of Monte Carlo simulation cubes, each representing a discrete scenario. This must be a 4
            dimensional object

        Returns
        -------
        DiscreteUncertaintyOptimizer
            Own instance
        """
        self._validate_num_scenarios(scenarios)
        self._obj_fun = [self._format_func(fn, cube) for cube in scenarios]

        self._max_or_min = 'maximize'
        self.set_stopval(float("inf"))

        return self

    def set_min_objective(self, fn: Callable, scenarios: Cubes):
        """
        Sets the optimizer to minimize the objective function. If gradient of the objective function is not set and the
        algorithm used to optimize is gradient-based, the optimizer will attempt to insert a smart numerical gradient
        for it.

        Parameters
        ----------
        fn: Callable
            Objective function

        scenarios
            A list of Monte Carlo simulation cubes, each representing a discrete scenario. This must be a 4
            dimensional object

        Returns
        -------
        DiscreteUncertaintyOptimizer
            Own instance
        """
        self._validate_num_scenarios(scenarios)
        self._obj_fun = [self._format_func(fn, cube) for cube in scenarios]

        self._max_or_min = 'minimize'
        self._model.set_stopval(-float('inf'))

        return self

    def add_inequality_constraint(self, fn: Callable, scenarios: Cubes):
        """
        Adds the equality constraint function in standard form, A <= b. If the gradient of the constraint function is
        not specified and the algorithm used is a gradient-based one, the optimizer will attempt to insert a smart
        numerical gradient for it.

        Parameters
        ----------
        fn: Callable
            Constraint function

        scenarios
            A list of Monte Carlo simulation cubes, each representing a discrete scenario. This must be a 4
            dimensional object

        Returns
        -------
        DiscreteUncertaintyOptimizer
            Own instance
        """
        self._validate_num_scenarios(scenarios)
        self._hin[fn.__name__] = [self._set_gradient(self._format_func(fn, cube)) for cube in scenarios]
        return self

    def add_equality_constraint(self, fn: Callable, scenarios: Cubes):
        """
        Adds the equality constraint function in standard form, A = b. If the gradient of the constraint function
        is not specified and the algorithm used is a gradient-based one, the optimizer will attempt to insert a smart
        numerical gradient for it.

        Parameters
        ----------
        fn: Callable
            Constraint function

        scenarios
            A list of Monte Carlo simulation cubes, each representing a discrete scenario. This must be a 4
            dimensional object

        Returns
        -------
        DiscreteUncertaintyOptimizer
            Own instance
        """
        self._validate_num_scenarios(scenarios)
        self._heq[fn.__name__] = [self._set_gradient(self._format_func(fn, cube)) for cube in scenarios]
        return self

    def add_inequality_matrix_constraint(self, A, b):
        r"""
        Sets inequality constraints in standard matrix form.

        For inequality, :math:`\mathbf{A} \cdot \mathbf{x} \leq \mathbf{b}`

        Parameters
        ----------
        A: {iterable float, ndarray}
            Inequality matrix. Must be 2 dimensional.

        b: {scalar, ndarray}
            Inequality vector or scalar. If scalar, it will be propagated.

        Returns
        -------
        DiscreteUncertaintyOptimizer
            Own instance
        """
        A, b = validate_matrix_constraints(A, b)

        for i, row, _b in zip(range(len(b)), A, b):
            self._min[f'A_{i}'] = create_gradient_func(create_matrix_constraint(row, _b), self._eps)

        return self

    def add_equality_matrix_constraint(self, Aeq, beq):
        r"""
        Sets equality constraints in standard matrix form.

        For equality, :math:`\mathbf{A} \cdot \mathbf{x} = \mathbf{b}`

        Parameters
        ----------
        Aeq: {iterable float, ndarray}
            Equality matrix. Must be 2 dimensional

        beq: {scalar, ndarray}
            Equality vector or scalar. If scalar, it will be propagated

        Returns
        -------
        DiscreteUncertaintyOptimizer
            Own instance
        """
        Aeq, beq = validate_matrix_constraints(Aeq, beq)

        for i, row, _beq in zip(range(len(beq)), Aeq, beq):
            self._meq[f'Aeq_{i}'] = create_gradient_func(create_matrix_constraint(row, _beq), self._eps)

        return self

    def remove_all_constraints(self):
        """Removes all constraints"""
        self._hin = {}
        self._min = {}
        self._heq = {}
        self._meq = {}
        return self

    def set_bounds(self, lb: Numeric, ub: Numeric):
        """
        Sets the lower and upper bound

        Parameters
        ----------
        lb: {int, float, ndarray}
            Vector of lower bounds. If array, must be same length as number of free variables. If :class:`float` or
            :class:`int`, value will be propagated to all variables.

        ub: {int, float, ndarray}
            Vector of upper bounds. If array, must be same length as number of free variables. If :class:`float` or
            :class:`int`, value will be propagated to all variables.

        Returns
        -------
        DiscreteUncertaintyOptimizer
            Own instance
        """
        self.set_lower_bounds(lb)
        self.set_upper_bounds(ub)

        return self

    def set_lower_bounds(self, lb: Numeric):
        """
        Sets the lower bounds

        Parameters
        ----------
        lb: {int, float, ndarray}
            Vector of lower bounds. If vector, must be same length as number of free variables. If :class:`float` or
            :class:`int`, value will be propagated to all variables.

        Returns
        -------
        DiscreteUncertaintyOptimizer
            Own instance
        """
        if isinstance(lb, (int, float)):
            lb = np.repeat(float(lb), self._num_assets)

        assert len(lb) == self._num_assets, f"Input vector length must be {self._num_assets}"
        self._lb = np.asarray(lb)
        return self

    def set_upper_bounds(self, ub: Numeric):
        """
        Sets the upper bound

        Parameters
        ----------
        ub: {int, float, ndarray}
            Vector of lower bounds. If vector, must be same length as number of free variables. If :class:`float` or
            :class:`int`, value will be propagated to all variables.

        Returns
        -------
        DiscreteUncertaintyOptimizer
            Own instance
        """
        if isinstance(ub, (int, float)):
            ub = np.repeat(float(ub), self._num_assets)

        assert len(ub) == self._num_assets, f"Input vector length must be {self._num_assets}"
        self._ub = np.asarray(ub)
        return self

    def set_epsilon(self, eps: float):
        """
        Sets the step difference used when calculating the gradient for derivative based optimization algorithms.
        This can ignored if you use a derivative free algorithm or if you specify your gradient specifically.

        Parameters
        ----------
        eps: float
            The gradient step

        Returns
        -------
        DiscreteUncertaintyOptimizer
            Own instance
        """
        assert isinstance(eps, float), "Epsilon must be a float"

        self._eps = eps
        return self

    def set_epsilon_constraint(self, eps: float):
        """
        Sets the tolerance for the constraint functions

        Parameters
        ----------
        eps: float
            Tolerance

        Returns
        -------
        DiscreteUncertaintyOptimizer
            Own instance
        """
        assert isinstance(eps, float), "Epsilon must be a float"

        self._c_eps = eps
        return self

    def set_xtol_abs(self, tol: Union[float, np.ndarray]):
        """
        Sets absolute tolerances on optimization parameters.

        The tol input must be an array of length `n` specified in the initialization. Alternatively, pass a single
        number in order to set the same tolerance for all optimization parameters.

        Set to None if no tolerance is needed.

        Parameters
        ----------
        tol: {float, ndarray}
            Absolute tolerance for each of the free variables

        Returns
        -------
        DiscreteUncertaintyOptimizer
            Own instance
        """
        self._x_tol_abs = validate_tolerance(tol)
        return self

    def set_xtol_rel(self, tol: Union[float, np.ndarray]):
        """
        Sets relative tolerances on optimization parameters.

        The tol input must be an array of length `n` specified in the initialization. Alternatively, pass a single
        number in order to set the same tolerance for all optimization parameters.

        Set to None if no tolerance is needed.

        Parameters
        ----------
        tol: {float, ndarray}
            relative tolerance for each of the free variables

        Returns
        -------
        DiscreteUncertaintyOptimizer
            Own instance
        """
        self._x_tol_rel = validate_tolerance(tol)
        return self

    def set_maxeval(self, n: int):
        """
        Sets maximum number of objective function evaluations.

        After maximum number of evaluations, optimization will stop. Set 0 or negative for no limit.

        Parameters
        ----------
        n: int
            maximum number of evaluations

        Returns
        -------
        DiscreteUncertaintyOptimizer
            Own instance
        """
        assert isinstance(n, int), "max evaluation must be an integer"
        self._max_eval = n
        return self

    def set_ftol_abs(self, tol: float):
        """
        Set absolute tolerance on objective function value.

        Set to None if no tolerance is needed.

        Parameters
        ----------
        tol: float
            absolute tolerance of objective function value

        Returns
        -------
        DiscreteUncertaintyOptimizer
            Own instance
        """
        self._f_tol_abs = validate_tolerance(tol)
        return self

    def set_ftol_rel(self, tol: Optional[float]):
        """
        Set relative tolerance on objective function value.

        Set to None if no tolerance is needed.

        Parameters
        ----------
        tol: float, optional
            Absolute relative of objective function value

        Returns
        -------
        DiscreteUncertaintyOptimizer
            Own instance
        """
        self._f_tol_rel = validate_tolerance(tol)
        return self

    def set_stopval(self, stopval: Optional[float]):
        """
        Stop when an objective value of at least/most stopval is found depending on min or max objective.

        Set to None if there are no stop values.

        Parameters
        ----------
        stopval: float, optional
            Stopping value

        Returns
        -------
        DiscreteUncertaintyOptimizer
            Own instance
        """
        assert isinstance(stopval, (int, float)) or stopval is None, "stop value must be a number or None"
        self._stop_val = stopval
        return self

    def set_prob(self, prob: OptArray):
        """
        Sets the probability of each scenario happening. If prob is set to None, it will default to equal
        weighting each scenario

        Parameters
        ----------
        prob
            Vector containing probability of each scenario happening

        Returns
        -------
        DiscreteUncertaintyOptimizer
            Own instance
        """
        if prob is None:
            prob = np.ones(self._num_scenarios) / self._num_scenarios

        self._prob = np.asarray(prob)
        return self

    @abstractmethod
    def optimize(self, x0: OptArray, tol: OptReal = None, random_start=False):
        raise NotImplementedError

    @abstractmethod
    def summary(self):
        raise NotImplementedError

    @staticmethod
    def _format_func(fn: Callable[[np.ndarray, np.ndarray], float], cube: np.ndarray):
        """Formats the objective or constraint function"""
        assert callable(fn), "Argument must be a function"
        f = partial(fn, np.asarray(cube))
        f.__name__ = fn.__name__
        return f

    def _set_gradient(self, fn):
        """Sets a numerical gradient for the function if the gradient is not specified"""
        assert callable(fn), "Argument must be a function"
        if self._auto_grad and len(inspect.signature(fn).parameters) == 1:
            if self._verbose:
                print(f"Setting gradient for function: '{fn.__name__}'")
            return create_gradient_func(fn, self._eps)
        else:
            return fn

    def _validate_num_scenarios(self, scenarios: Cubes):
        error_msg = f"Number of scenarios do not match. Scenarios given: {len(scenarios)}. " \
            f"Scenarios expected: {self._num_scenarios}"
        assert len(scenarios) == self._num_scenarios, error_msg
