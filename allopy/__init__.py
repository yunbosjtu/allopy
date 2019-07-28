from allopy._config import get_option, set_option
from allopy.opt_data import OptData
from allopy.optimize import ActivePortfolioOptimizer, BaseOptimizer, PortfolioOptimizer, RegretOptimizer
from ._version import get_versions

v = get_versions()
__version__ = v.get('version', '0.0.0').split("+")[0]

del v, get_versions
