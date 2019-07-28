from typing import Callable, Optional, Union

import nlopt as nl
import numpy as np

from allopy.optimize import BaseOptimizer
from allopy.types import OptArray
from .funcs import *
from ..algorithms import LD_SLSQP
from ..uncertainty import DiscreteUncertaintyOptimizer

__all__ = ['RegretOptimizer']


class RegretOptimizer(DiscreteUncertaintyOptimizer):
    def __init__(self,
                 num_assets: int,
                 num_scenarios: int,
                 prob: OptArray = None,
                 algorithm=LD_SLSQP,
                 auto_grad: Optional[bool] = None,
                 eps_step: Optional[float] = None,
                 c_eps: Optional[float] = None,
                 xtol_abs: Union[float, np.ndarray, None] = None,
                 xtol_rel: Union[float, np.ndarray, None] = None,
                 ftol_abs: Optional[float] = None,
                 ftol_rel: Optional[float] = None,
                 max_eval: Optional[int] = None,
                 stopval: Optional[float] = None,
                 verbose=False,
                 max_attempts=100):
        r"""
        The RegretOptimizer houses several common pre-specified optimization regimes for scenario based
        optimization.

        Notes
        -----
        The term regret refers to the instance where after having decided on one alternative, the choice
        of a different alternative would have led to a more optimal (better) outcome when the eventual
        scenario transpires.

        The RegretOptimizer employs a 2 stage optimization process. In the first step, the optimizer
        calculates the optimal weights for each scenario. In the second stage, the optimizer minimizes
        the regret function to give the final optimal portfolio weights.

        Assuming the objective is to maximize returns subject to some volatility constraints, the first
        stage optimization will be as listed

        .. math::

            \begin{gather*}
                \underset{w_s}{\max} R_s(w_s)  \forall s \in S \\
                s.t. \\
                \sigma_s(w_s) \leq \Sigma
            \end{gather*}

        where :math:`R_s(\cdot)` is the returns function for scenario :math:`s`, :math:`\sigma_s(\cdot)`
        is the volatility function for scenario :math:`s` and :math:`\Sigma` is the volatility threshold.
        Subsequently, to minimize the regret across all scenarios, :math:`S`,

        .. math::

            \begin{gather*}
                \underset{w}{\min} \sum_{s \in S} p_s \cdot D(R_s(w_s) - R_s(w))
            \end{gather*}

        Where :math:`D(\cdot)` is a distance function (usually quadratic) and :math:`p_s` is the discrete
        probability of scenario :math:`s` occurring.

        Parameters
        ----------
        num_assets: int
            Number of assets

        num_scenarios: int
            Number of scenarios

        prob
            Vector containing probability of each scenario occurring

        algorithm
            The optimization algorithm. Default algorithm is Sequential Least Squares Quadratic Programming.

        auto_grad: bool, optional
            If True, the optimizer will calculate numeric gradients for functions that do not specify its
            gradients. The symmetric difference quotient method is used in this case

        eps_step: float, optional
            Epsilon, smallest degree of change, for numeric gradients

        c_eps: float, optional
            Constraint epsilon is the tolerance for the inequality and equality constraints functions. Any
            value that is less than the constraint epsilon is considered to be within the boundary.

        xtol_abs: float or np.ndarray, optional
            Set absolute tolerances on optimization parameters. :code:`tol` is an array giving the
            tolerances: stop when an optimization step (or an estimate of the optimum) changes every
            parameter :code:`x[i]` by less than :code:`tol[i]`. For convenience, if a scalar :code:`tol`
            is given, it will be used to set the absolute tolerances in all n optimization parameters to
            the same value. Criterion is disabled if tol is non-positive.

        xtol_rel: float or np.ndarray, optional
            Set relative tolerance on optimization parameters: stop when an optimization step (or an estimate
            of the optimum) causes a relative change the parameters :code:`x` by less than :code:`tol`,
            i.e. :math:`\|\Delta x\|_w < tol \cdot \|x\|_w` measured by a weighted :math:`L_1` norm
            :math:`\|x\|_w = \sum_i w_i |x_i|`, where the weights :math:`w_i` default to 1. (If there is
            any chance that the optimal :math:`\|x\|` is close to zero, you might want to set an absolute
            tolerance with `code:`xtol_abs` as well.) Criterion is disabled if tol is non-positive.

        ftol_abs: float, optional
            Set absolute tolerance on function value: stop when an optimization step (or an estimate of
            the optimum) changes the function value by less than :code:`tol`. Criterion is disabled if
            tol is non-positive.

        ftol_rel: float, optional
            Set relative tolerance on function value: stop when an optimization step (or an estimate of
            the optimum) changes the objective function value by less than :code:`tol` multiplied by the
            absolute value of the function value. (If there is any chance that your optimum function value
            is close to zero, you might want to set an absolute tolerance with :code:`ftol_abs` as well.)
            Criterion is disabled if tol is non-positive.

        max_eval: int, optional
            Stop when the number of function evaluations exceeds :code:`maxeval`. (This is not a strict
            maximum: the number of function evaluations may exceed :code:`maxeval` slightly, depending
            upon the algorithm.) Criterion is disabled if maxeval is non-positive.

        stopval: int, optional
            Stop when an objective value of at least :code:`stopval` is found: stop minimizing when an
            objective value ≤ :code:`stopval` is found, or stop maximizing a value ≥ :code:`stopval` is
            found. (Setting :code:`stopval` to :code:`-HUGE_VAL` for minimizing or :code:`+HUGE_VAL` for
            maximizing disables this stopping criterion.)

        verbose: bool
            If True, the optimizer will report its operations

        max_attempts: int
            Number of times to retry optimization. This is useful when optimization is in a highly unstable
            or non-convex space.

        See Also
        --------
        :class:`DiscreteUncertaintyOptimizer`: Discrete Uncertainty Optimizer
        """
        super().__init__(num_assets, num_scenarios, prob, algorithm, auto_grad, eps_step, c_eps,
                         xtol_abs, xtol_rel, ftol_abs, ftol_rel, max_eval, stopval, verbose)

        self._max_attempts = 0
        self.max_attempts = max_attempts

    def optimize(self,
                 x0: OptArray = None,
                 random_start=False,
                 approx=True,
                 dist_func: Callable[[np.ndarray], np.ndarray] = lambda x: x ** 2):
        r"""
        Finds the minimal regret solution across the range of scenarios

        Notes
        -----
        The exact (actual) objective function to minimize regret is given below,

        .. math::

            \begin{gather*}
                \underset{w}{\min} \sum_{s \in S} p_s \cdot D(R_s(w_s) - R_s(w))
            \end{gather*}

        However, given certain problem formulations where the objective and constraint functions are
        linear and convex, the problem can be transformed to

        .. math::

            \begin{gather*}
                \underset{a}{\min} \sum_{s \in S} p_s \cdot D(R_s(w_s) - R_s(W \cdot a))
            \end{gather*}

        where :math:`W` is a matrix where each rows represents a single scenario, :math:`s` and each
        column represents an asset class. This formulation solves for :math:`a` which represents the
        proportion of each scenario that contributes to the final portfolio weights. Thus if there are
        3 scenarios and :math:`a` is :code:`[0.3, 0.5, 0.2]`, it means that the final portfolio took
        30% from scenario 1, 50% from scenario 2 and 20% from scenario 3.

        This formulation makes a strong assumption that the final minimal regret portfolio is a linear
        combination of the weights from each scenario's optimal.

        Parameters
        ----------
        x0: ndarray, optional
            Initial solution vector to start the optimization. Use this if there is already an opinion of
            where the solution will be.

        random_start: bool
            If True, a random initial vector will be used to start the optimization.

        approx: bool
            If True, a linear approximation will be used to calculate the regret optimal. See Notes.

        dist_func: Callable
            A callable function that will be applied as a distance metric for the regret function. The
            default is a quadratic function. See Notes.

        Returns
        -------
        ndarray
            Optimal solution weights
        """
        # optimal solution to each scenario. Each row represents a single scenario and
        # each column represents an asset class
        solutions = np.array([
            self._optimize(
                self._setup_optimization_model(i),
                x0,
                random_start
            ) for i in range(self._num_scenarios)
        ])

        if approx:
            props, weights = self._optimize_approx(solutions, dist_func)
        else:
            weights = self._optimize_actual(solutions, dist_func)
            props = None

        self._result.update(self._c_eps, self._hin, self._heq, self._min, self._meq, weights, props)

        return weights

    def _optimize(self, model: BaseOptimizer, x0: OptArray = None, random_start=False):
        """Helper method to run the model"""

        for _ in range(self.max_attempts):
            try:
                w = model.optimize(x0, random_start=random_start)
                if w is not None:
                    return w

            except (nl.RoundoffLimited, RuntimeError):
                x0 = np.random.uniform(self.lower_bounds, self.upper_bounds)
        else:
            if self._verbose:
                print('No solution was found for the given problem. Check the summary() for more information')
            return np.repeat(np.nan, self._num_assets)

    def _optimize_actual(self, solutions: np.ndarray, dist_func: Callable):
        """
        Runs the second step (regret minimization) using the actual weights as the decision variable

        Parameters
        ----------
        solutions
            Matrix of solution where the rows represents the weights for the scenario and the
            columns represent the asset classes

        dist_func: Callable
            Distance function to scale the objective function
        """
        f_values = np.array(self._obj_fun[i](s) for i, s in enumerate(solutions))

        def regret(w):
            curr_f_values = np.array([f(w) for f in self._obj_fun])
            cost = f_values - curr_f_values
            if callable(dist_func):
                cost = np.asarray(dist_func(cost))
            return sum(self.prob * cost)

        model = BaseOptimizer(self._num_scenarios)
        model.set_min_objective(regret)
        model.set_bounds(self.lower_bounds, self.upper_bounds)

        for constraints, set_constraint in [(self._meq.values(), model.add_equality_matrix_constraint),
                                            (self._min.values(), model.add_inequality_matrix_constraint)]:
            for c in constraints:
                set_constraint(self._set_gradient(c), self._c_eps)

        return self._optimize(model)

    def _optimize_approx(self, solutions: np.ndarray, dist_func: Callable):
        """
        Runs the second step (regret minimization) where the decision variable

        Parameters
        ----------
        solutions
            Matrix of solution where the rows represents the weights for the scenario and the
            columns represent the asset classes

        dist_func: Callable
            Distance function to scale the objective function
        """

        # weighted function values for each scenario
        f_values: np.ndarray = np.array([self._obj_fun[i](s) for i, s in enumerate(solutions)])

        def regret(p):
            curr_f_values = np.array([self._obj_fun[i](p @ s) for i, s in enumerate(solutions)])
            cost = f_values - curr_f_values
            if callable(dist_func):
                cost = np.asarray(dist_func(cost))
            return sum(self.prob * cost)

        model = BaseOptimizer(self._num_scenarios)
        model.set_min_objective(regret)
        model.add_equality_constraint(sum_to_1)
        model.set_bounds(0, 1)

        proportions = self._optimize(model)
        return proportions, proportions @ solutions

    @property
    def max_attempts(self):
        return self._max_attempts

    @max_attempts.setter
    def max_attempts(self, value: int):
        assert isinstance(value, int) and value > 0, 'max_attempts must be an integer >= 1'
        self._max_attempts = value

    def _setup_optimization_model(self, index: int):
        """Sets up the Base Optimizer"""
        model = BaseOptimizer(self._num_assets, self._algorithm, verbose=self._verbose)

        # sets up optimizer's programs and bounds
        for item, set_option in [
            (self._x_tol_abs, model.set_xtol_abs),
            (self._x_tol_rel, model, model.set_xtol_rel),
            (self._f_tol_abs, model.set_ftol_abs),
            (self._f_tol_rel, model.set_ftol_rel),
            (self._max_eval, model.set_maxeval),
            (self._stop_val, model.set_stopval),
            (self._lb, model.set_lower_bounds),
            (self._ub, model.set_upper_bounds)
        ]:
            if item is not None:
                set_option(item)

        # sets constraints
        for constraints, set_constraint in [(self._min.values(), model.add_inequality_matrix_constraint),
                                            (self._meq.values(), model.add_equality_matrix_constraint)]:
            for c in constraints:
                set_constraint(self._set_gradient(c), self._c_eps)

        for constraints, set_constraint in [(self._heq.values(), model.add_equality_constraint),
                                            (self._hin.values(), model.add_inequality_constraint)]:
            set_constraint(self._set_gradient(constraints[index]), self._c_eps)

        # sets up the objective function
        assert self._max_or_min in ('maximize', 'mimimize') and len(self._obj_fun) == 0, \
            "Objective function is not set yet. Use the .set_max_objective() or .set_min_objective() methods to do so"

        if self._max_or_min == "maximize":
            model.set_max_objective(self._set_gradient(self._obj_fun[index]))
        else:
            model.set_min_objective(self._set_gradient(self._obj_fun[index]))

        return model