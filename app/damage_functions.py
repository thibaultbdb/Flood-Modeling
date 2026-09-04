"""
Flood impact (damage) functions, ported verbatim from GFDRR CCDR-tools
(tools/code/damageFunctions.py) so results match the reference notebook.

All functions expect water depth in CENTIMETRES (Fathom v3 native unit)
and return an impact factor in [0, 1].
"""
import numpy as np

# World Bank region -> damage-curve region (from CCDR-tools common.py)
WB_TO_REGION = {
    'AFR': 'AFRICA',    # Sub-Saharan Africa
    'MENA': 'AFRICA',   # Middle East and North Africa
    'EAP': 'ASIA',      # East Asia and Pacific
    'SAR': 'ASIA',      # South Asia
    'ECA': 'ASIA',      # East Europe and Central Asia
    'LCR': 'LAC',       # Latin America and Caribbean
    'Other': 'GLOBAL',  # North America, Europe, Japan, Korea, Australia, NZ
}


def FL_mortality_factor(x, wb_region=None):
    """Population mortality vs flood depth (Jonkman, 2008). Global curve."""
    x = x / 100.0  # cm -> m
    return np.maximum(0.0, np.minimum(1.0, 0.985 / (1 + np.exp(6.32 - 1.412 * x))))


def FL_damage_factor_builtup(x, wb_region):
    """Built-up damage vs depth (Huizinga et al. 2017, EU-JRC), regional curves."""
    x = (x / 100.0).astype(np.float32)  # cm -> m
    function_mapping = {
        'AFRICA': lambda x: np.maximum(0.0, np.minimum(1.0, 1.246282 + (0.004404681 - 1.246282) / (1 + (x / 1.888094) ** 1.245007))),
        'ASIA':   lambda x: np.maximum(0.0, np.minimum(1.0, 1.267385 + (0.002553797 - 1.267385) / (1 + (x / 1.511393) ** 1.011526))),
        'LAC':    lambda x: np.maximum(0.0, np.minimum(1.0, 1.04578 + (0.001490579 - 1.04578) / (1 + (x / 0.5619431) ** 1.509554))),
        'GLOBAL': lambda x: np.maximum(0.0, np.minimum(1.0, 2.100049 + (-0.00003530885 - 2.100049) / (1 + (x / 6.632485) ** 0.559315))),
    }
    region = WB_TO_REGION.get(wb_region, 'GLOBAL')
    result = function_mapping[region](x)
    return result.astype(np.float32)


def FL_damage_factor_agri(x, wb_region):
    """Agriculture damage vs depth (Huizinga et al. 2017, EU-JRC), regional curves."""
    x = (x / 100.0).astype(np.float64)  # cm -> m; float64: AFRICA curve overflows float32
    with np.errstate(over='ignore'):
        function_mapping = {
            'AFRICA': lambda x: np.maximum(0.0, np.minimum(1.0, 1.006324 + (0.01417282 - 1.006324) / (1 + (x / 8621.368) ** 1.675571) ** 2665027)),
            'ASIA':   lambda x: np.maximum(0.0, np.minimum(1.0, (1.672909 * x) / (3.917017 + x))),
            'LAC':    lambda x: np.maximum(0.0, np.minimum(1.0, 1.876076 + (0.01855393 - 1.876076) / (1 + (x / 5.08262) ** 0.7629432))),
            'GLOBAL': lambda x: np.maximum(0.0, np.minimum(1.0, 1.167022 + (-0.002602531 - 1.167022) / (1 + (x / 1.398796) ** 1.246833))),
        }
        region = WB_TO_REGION.get(wb_region, 'GLOBAL')
        result = function_mapping[region](x)
    return result.astype(np.float32)


DAMAGE_FUNCTIONS = {
    'POP': FL_mortality_factor,
    'BU': FL_damage_factor_builtup,
    'AGR': FL_damage_factor_agri,
}


def get_damage_function(exp_cat):
    try:
        return DAMAGE_FUNCTIONS[exp_cat]
    except KeyError:
        raise ValueError(f"Unknown exposure category: {exp_cat}")
