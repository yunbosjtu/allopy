import pytest

from allopy import PortfolioRegretOptimizer
from allopy.optimize.regret.summary import RegretSummary
from .data import Test1, assets, scenarios


@pytest.mark.parametrize("config", [Test1])
def test_regret_optimizer_summary(config, main_cubes, cvar_cubes):
    opt = PortfolioRegretOptimizer(main_cubes, cvar_cubes, config.prob.as_array())
    opt.set_bounds(config.lb.as_array(), config.ub.as_array())

    opt.maximize_returns(max_cvar=config.cvar.as_array())
    opt.set_meta(assets=assets, scenarios=scenarios)
    assert isinstance(opt.summary(), RegretSummary)
