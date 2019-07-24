import inspect
from typing import Callable, Optional, Union

import nlopt as nl
import numpy as np
from copulae.types import Numeric

from allopy import get_option
from allopy.types import OptArray, OptNumeric
from .result import ConstraintMap, Result
from .summary import Summary
from ..algorithms import LD_SLSQP, has_gradient, map_algorithm
from ..utils import *

__all__ = ['BaseOptimizer']

Tolerance = Union[int, float, np.ndarray, None]


class BaseOptimizer:
    _A: np.ndarray
    _b: np.ndarray
    _Aeq: np.ndarray
    _beq: np.ndarray

    def __init__(self, n: int, algorithm=LD_SLSQP, *args, **kwargs):
        """
        The BaseOptimizer is the raw optimizer with minimal support. For advanced users, this class will provide
        the most flexibility. The default algorithm used is Sequential Least Squares Quadratic Programming.

        Parameters
        ----------
        n: int
            number of assets

        algorithm: int or str
            the optimization algorithm

        args
            other arguments to setup the optimizer

        kwargs
            other keyword arguments
        """
        if isinstance(algorithm, str):
            algorithm = map_algorithm(algorithm)

        self._n = n
        self._model = nl.opt(algorithm, n, *args)

        has_grad = has_gradient(algorithm)
        if has_grad == 'NOT COMPILED':
            raise NotImplementedError(f"Cannot use '{nl.algorithm_name(algorithm)}' as it is not compiled")

        self._auto_grad: bool = kwargs.get('auto_grad', has_grad)
        self._eps = get_option('EPS.STEP')
        self._c_eps = get_option('EPS.CONSTRAINT')
        self.set_xtol_abs(get_option('EPS.X_ABS'))
        self.set_ftol_abs(get_option('EPS.FUNCTION'))
        self.set_maxeval(get_option('MAX.EVAL'))

        self._hin: ConstraintMap = {}
        self._heq: ConstraintMap = {}
        self._result: Optional[Result] = None
        self._max_or_min = None
        self._verbose = kwargs.get('verbose', False)

    @property
    def lower_bounds(self):
        """Lower bound of each variable"""
        return np.asarray(self._model.get_lower_bounds(), np.float64)

    @lower_bounds.setter
    def lower_bounds(self, lb: OptNumeric):
        self.set_lower_bounds(lb)

    @property
    def upper_bounds(self):
        """Upper bound of each variable"""
        return np.asarray(self._model.get_upper_bounds(), np.float64)

    @upper_bounds.setter
    def upper_bounds(self, ub: OptNumeric):
        self.set_upper_bounds(ub)

    def optimize(self, x0: OptArray = None, *args, random_start=False) -> np.ndarray:
        """
        Runs the optimizer and returns the optimal results if any.

        If no initial vector is set, the optimizer will ATTEMPT to randomly generate a feasible one. However,
        there is no guarantee in the feasibility. In general, it is a tough problem to find a feasible solution
        in high-dimensional spaces, much more an optimal one.

        Parameters
        ----------
        x0: iterable float
            Initial vector. Starting position for free variables. In many cases, especially for derivative-based
            optimizers, it is important for the initial vector to be already feasible.

        args
            other arguments to pass into the optimizer

        random_start: bool
            If True, the optimizer will randomly generate "bound-feasible" starting points for the decision
            variables. Note that these variables may not fulfil the other constraints. For problems where the
            bounds have been tightly defined, this often yields a good solution.

        Returns
        -------
        ndarray
            Values of free variables at optimality
        """
        if x0 is None and random_start:
            lb = self.lower_bounds
            ub = self.upper_bounds

            if np.isinf(lb).any() or np.isinf(ub).any():
                x0 = np.random.uniform(size=self._n)
            else:
                x0 = np.random.uniform(lb, ub)

        elif hasattr(x0, "__iter__"):
            x0 = np.asarray(x0)

        sol = self._model.optimize(x0, *args)
        if sol is not None:
            self._result = Result(self._hin, self._heq, sol, self._eps)
            return self._result.x
        else:
            if self._verbose:
                print('No solution was found for the given problem. Check the summary() for more information')
            return np.repeat(np.nan, len(x0))

    def set_max_objective(self, fn: Callable, *args):
        """
        Sets the optimizer to maximize the objective function. If gradient of the objective function is not set and the
        algorithm used to optimize is gradient-based, the optimizer will attempt to insert a smart numerical gradient
        for it.

        Parameters
        ----------
        fn: Callable
            Objective function

        args
            Other arguments to pass to the objective function. This can be ignored in most cases

        Returns
        -------
        BaseOptimizer
            Own instance
        """
        self._max_or_min = 'maximize'
        self._model.set_stopval(float('inf'))
        self._model.set_max_objective(self._set_gradient(fn), *args)

        return self

    def set_min_objective(self, fn: Callable, *args):
        """
        Sets the optimizer to minimize the objective function. If gradient of the objective function is not set and the
        algorithm used to optimize is gradient-based, the optimizer will attempt to insert a smart numerical gradient
        for it.

        Parameters
        ----------
        fn: Callable
            Objective function

        args
            Other arguments to pass to the objective function. This can be ignored in most cases

        Returns
        -------
        BaseOptimizer
            Own instance
        """
        self._max_or_min = 'minimize'
        self._model.set_stopval(-float('inf'))
        self._model.set_min_objective(self._set_gradient(fn), *args)

        return self

    def add_inequality_constraint(self, fn: Callable, tol=None):
        """
        Adds the equality constraint function in standard form, A <= b. If the gradient of the constraint function is
        not specified and the algorithm used is a gradient-based one, the optimizer will attempt to insert a smart
        numerical gradient for it.

        Parameters
        ----------
        fn: Callable
            Constraint function

        tol: float, optional
            A tolerance in judging feasibility for the purposes of stopping the optimization

        Returns
        -------
        BaseOptimizer
            Own instance
        """
        tol = self._c_eps if tol is None else tol

        self._hin[fn.__name__] = fn
        self._model.add_inequality_constraint(self._set_gradient(fn), tol)
        return self

    def add_equality_constraint(self, fn: Callable, tol=None):
        """
        Adds the equality constraint function in standard form, A = b. If the gradient of the constraint function
        is not specified and the algorithm used is a gradient-based one, the optimizer will attempt to insert a smart
        numerical gradient for it.

        Parameters
        ----------
        fn: Callable
            Constraint function

        tol: float, optional
            A tolerance in judging feasibility for the purposes of stopping the optimization

        Returns
        -------
        BaseOptimizer
            Own instance
        """
        tol = self._c_eps if tol is None else tol

        self._heq[fn.__name__] = fn
        self._model.add_equality_constraint(self._set_gradient(fn), tol)
        return self

    def add_inequality_matrix_constraint(self, A, b, tol=None):
        r"""
        Sets inequality constraints in standard matrix form.

        For inequality, :math:`\mathbf{A} \cdot \mathbf{x} \leq \mathbf{b}`

        Parameters
        ----------
        A
            Inequality matrix. Must be 2 dimensional.

        b
            Inequality vector or scalar. If scalar, it will be propagated.

        tol
            A tolerance in judging feasibility for the purposes of stopping the optimization

        Returns
        -------
        BaseOptimizer
            Own instance
        """
        tol = self._c_eps if tol is None else tol

        A, b = validate_matrix_constraints(A, b)

        for i, row, _b in zip(range(len(b)), A, b):
            fn = create_matrix_constraint(row, _b)
            f = create_gradient_func(fn, self._eps)
            self._model.add_inequality_constraint(f, tol)
            self._hin[f'A_{i}'] = fn

        return self

    def add_equality_matrix_constraint(self, Aeq, beq, tol=None):
        r"""
        Sets equality constraints in standard matrix form.

        For equality, :math:`\mathbf{A} \cdot \mathbf{x} = \mathbf{b}`

        Parameters
        ----------
        Aeq
            Equality matrix. Must be 2 dimensional

        beq
            Equality vector or scalar. If scalar, it will be propagated

        tol
            A tolerance in judging feasibility for the purposes of stopping the optimization

        Returns
        -------
        BaseOptimizer
            Own instance
        """
        tol = self._c_eps if tol is None else tol

        Aeq, beq = validate_matrix_constraints(Aeq, beq)

        for i, row, _beq in zip(range(len(beq)), Aeq, beq):
            fn = create_matrix_constraint(row, _beq)
            f = create_gradient_func(fn, self._eps)
            self._model.add_equality_constraint(f, tol)
            self._heq[f'Aeq_{i}'] = fn

        return self

    def remove_all_constraints(self):
        """Removes all constraints"""
        self._model.remove_inequality_constraints()
        self._model.remove_equality_constraints()
        return self

    def set_bounds(self, lb: Numeric, ub: Numeric):
        """
        Sets the lower and upper bound

        Parameters
        ----------
        lb
            Vector of lower bounds. If array, must be same length as number of free variables. If :class:`float` or
            :class:`int`, value will be propagated to all variables.

        ub
            Vector of upper bounds. If array, must be same length as number of free variables. If :class:`float` or
            :class:`int`, value will be propagated to all variables.

        Returns
        -------
        BaseOptimizer
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
        lb
            Vector of lower bounds. If vector, must be same length as number of free variables. If :class:`float` or
            :class:`int`, value will be propagated to all variables.

        Returns
        -------
        BaseOptimizer
            Own instance
        """
        if isinstance(lb, (int, float)):
            lb = np.repeat(float(lb), self._n)

        assert len(lb) == self._n, f"Input vector length must be {self._n}"

        self._model.set_lower_bounds(np.asarray(lb))
        return self

    def set_upper_bounds(self, ub: Numeric):
        """
        Sets the upper bound

        Parameters
        ----------
        ub
            Vector of lower bounds. If vector, must be same length as number of free variables. If :class:`float` or
            :class:`int`, value will be propagated to all variables.

        Returns
        -------
        BaseOptimizer
            Own instance
        """
        if isinstance(ub, (int, float)):
            ub = np.repeat(float(ub), self._n)

        assert len(ub) == self._n, f"Input vector length must be {self._n}"

        self._model.set_upper_bounds(np.asarray(ub))
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
        BaseOptimizer
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
        BaseOptimizer
            Own instance
        """
        assert isinstance(eps, float), "Epsilon must be a float"
        self._c_eps = eps
        return self

    def set_xtol_abs(self, tol: Tolerance):
        """
        Sets absolute tolerances on optimization parameters.

        The tol input must be an array of length `n` specified in the initialization. Alternatively, pass a single
        number in order to set the same tolerance for all optimization parameters.

        Parameters
        ----------
        tol: {float, ndarray}
            Absolute tolerance for each of the free variables

        Returns
        -------
        BaseOptimizer
            Own instance
        """
        self._model.set_xtol_abs(validate_tolerance(tol))
        return self

    def set_xtol_rel(self, tol: Tolerance):
        """
        Sets relative tolerances on optimization parameters.

        The tol input must be an array of length `n` specified in the initialization. Alternatively, pass a single
        number in order to set the same tolerance for all optimization parameters.

        Parameters
        ----------
        tol: float or ndarray, optional
            relative tolerance for each of the free variables

        Returns
        -------
        BaseOptimizer
            Own instance
        """
        self._model.set_xtol_rel(validate_tolerance(tol))
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
        BaseOptimizer
            Own instance
        """
        assert isinstance(n, int), "max eval must be an integer"
        self._model.set_maxeval(n)
        return self

    def set_ftol_abs(self, tol: Tolerance):
        """
        Set absolute tolerance on objective function value

        Parameters
        ----------
        tol: float
            absolute tolerance of objective function value

        Returns
        -------
        BaseOptimizer
            Own instance
        """
        self._model.set_ftol_abs(validate_tolerance(tol))
        return self

    def set_ftol_rel(self, tol: Tolerance):
        """
        Set relative tolerance on objective function value

        Parameters
        ----------
        tol: float
            Absolute relative of objective function value

        Returns
        -------
        BaseOptimizer
            Own instance
        """
        self._model.set_ftol_rel(validate_tolerance(tol))
        return self

    def set_stopval(self, stopval: Optional[float]):
        """
        Stop when an objective value of at least/most stopval is found depending on min or max objective

        Parameters
        ----------
        stopval: float
            Stopping value

        Returns
        -------
        BaseOptimizer
            Own instance
        """
        self._model.set_stopval(validate_tolerance(stopval))
        return self

    def summary(self):
        """Prints a summary report of the optimizer"""

        model = self._model

        prog_setup = [
            ('objective', self._max_or_min),
            ('n_var', self._n),
            ('n_eq_con', len(self._heq)),
            ('n_ineq_con', len(self._hin)),
        ]

        opt_setup = [
            ('xtol_abs', model.get_xtol_abs()[0]),
            ('xtol_rel', model.get_xtol_rel()),
            ('ftol_abs', model.get_ftol_abs()),
            ('ftol_rel', model.get_ftol_rel()),
            ('max_eval', model.get_maxeval()),
            ('stop_val', model.get_stopval()),
        ]

        bounds = self.lower_bounds, self.upper_bounds
        smry = Summary(model.get_algorithm_name(), prog_setup, opt_setup, bounds)

        r = self._result
        if r is not None:
            smry.tight_hin = r.tight_hin
            smry.violations = r.violations
            smry.solution = r.x
        return smry

    def _set_gradient(self, fn: Callable):
        assert callable(fn), "Argument must be a function"

        if self._auto_grad and len(inspect.signature(fn).parameters) == 1:
            if self._verbose:
                print(f"Setting gradient for function: '{fn.__name__}'")
            return create_gradient_func(fn, self._eps)
        else:
            return fn
