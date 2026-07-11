# Hierarchical iterate-and-aggregate across a multi-level grid.
#
# Pattern: per-state data feeds a country-level feature-engineering task, which feeds a
# sector-level aggregation. The whole hierarchy is ONE DAG built from `requires()` fan-out over a
# plain nested enumeration (`UNIVERSE`) -- no flow-within-flow. Each aggregating task returns a
# dict of the level below and concatenates with `self.inputLoadConcat()`, which tags every frame
# with its dependency's params so the groupby keys (sector/country/state) survive.
#
# It also shows the real dev loop: you add a feature to the country-level task, iterate on ONE
# (sector, country) first, then roll the change out to every flow -- all WITHOUT ever re-fetching
# the expensive per-state source data.

import oryxflow
import pandas as pd

oryxflow.set_dir('data/')

# Plain domain config: the nested enumeration describing the hierarchy's shape.
# This is YOUR data (keep it in a cfg.py), not a oryxflow construct. `requires()` indexes into it.
UNIVERSE = {
    'Retail': {'US': ['CT', 'NY'], 'UK': ['London']},
    'Office': {'US': ['CA']},
}

# `ran` logs which task instances actually execute run(), so the output blocks below can prove
# exactly what recomputed (and, crucially, what did NOT). Not needed in real code.
ran = []


class DataLoadState(oryxflow.tasks.TaskCachePandas):
    """Leaf: per-state raw data. Imagine this is an expensive paid-API call you never want to
    re-run unless the raw data itself changes."""
    sector = oryxflow.Parameter()
    country = oryxflow.Parameter()
    state = oryxflow.Parameter()

    def run(self):
        ran.append(('DataLoadState', self.sector, self.country, self.state))
        self.save(pd.DataFrame({'v': range(2)}))


class CountryFeatures(oryxflow.tasks.TaskCachePandas):
    """Country-level feature engineering: aggregate a country's states, then derive features.
    This is the task you actively iterate on."""
    sector = oryxflow.Parameter()
    country = oryxflow.Parameter()

    def requires(self):
        return {s: DataLoadState(sector=self.sector, country=self.country, state=s)
                for s in UNIVERSE[self.sector][self.country]}

    def run(self):
        ran.append(('CountryFeatures', self.sector, self.country))
        df = self.inputLoadConcat()          # stacks states, keeps sector/country/state columns
        self.save(df.assign(feat=1))         # <- the "new feature" you develop below


class Sector(oryxflow.tasks.TaskCachePandas):
    """Sector-level aggregation: combine all of a sector's countries."""
    sector = oryxflow.Parameter()

    def requires(self):
        return {c: CountryFeatures(sector=self.sector, country=c) for c in UNIVERSE[self.sector]}

    def run(self):
        ran.append(('Sector', self.sector))
        self.save(self.inputLoadConcat())


# ---------------------------------------------------------------------------------------------
# Run every sector as its own flow, then concat across flows into one tagged frame.
# `params={'sector': [...]}` is the top-level params grid (which runs to launch) -- distinct from
# the UNIVERSE enumeration above (which shapes the DAG). See docs/source/workflow.rst.
# ---------------------------------------------------------------------------------------------
flow = oryxflow.WorkflowMulti(Sector, params={'sector': list(UNIVERSE)})
flow.preview(Sector, flow=0)
'''
+--[Sector-{'sector': 'Retail'} (PENDING)]
   |--[CountryFeatures- (PENDING)]
   |  |--[DataLoadState- (PENDING)]
   |  +--[DataLoadState- (PENDING)]
   +--[CountryFeatures- (PENDING)]
      +--[DataLoadState- (PENDING)]
   ... every state/country/sector shown -- the whole hierarchy is one DAG
'''

flow.run()
dfall = flow.outputLoadConcat(Sector)        # one frame, tagged by sector/country/state
'''
dfall.columns -> ['v', 'sector', 'country', 'state', 'feat']
len(dfall)    -> 8   (2 rows x 4 states)
dfall.groupby(['sector','country','state']).size()
    Office  US  CA        2
    Retail  UK  London    2
            US  CT        2
                NY        2
'''

# =============================================================================================
# DEV LOOP: add a new feature to CountryFeatures, iterate on ONE (sector, country) first.
# =============================================================================================
# Changing run() code does NOT auto-invalidate (oryxflow keys completeness on outputs+params, not
# code), so you reset the task whose code you changed. Root the run at the country instance so
# only Retail/US is touched: UK is a different instance the DAG never reaches from here, and the
# per-state DataLoadState outputs are already complete so the paid API is NOT called.
ran.clear()
flow_dev = oryxflow.Workflow(CountryFeatures, {'sector': 'Retail', 'country': 'US'})
flow_dev.reset()                             # reset only this instance
flow_dev.run()
'''
ran == [('CountryFeatures', 'Retail', 'US')]
  -> DataLoadState NOT called (states preserved)
  -> Retail/UK NOT touched (different instance, off the DAG rooted here)
'''

# =============================================================================================
# ROLL OUT: it works for US, so run it for every sector/country. `requires()` still enumerates
# all of them, so nothing about the DAG changes -- just reset CountryFeatures across ALL flows
# and run. `only=CountryFeatures` finds every CountryFeatures instance in the DAG; recursive
# complete() then forces the Sector aggregations to recompute. DataLoadState stays untouched.
# =============================================================================================
ran.clear()
flow.reset_upstream(Sector, only=CountryFeatures)
flow.run()
'''
CountryFeatures recomputed for every flow:
    ('CountryFeatures', 'Office', 'US')
    ('CountryFeatures', 'Retail', 'UK')
    ('CountryFeatures', 'Retail', 'US')
Sector recomputed: ('Sector', 'Office'), ('Sector', 'Retail')
  -> DataLoadState NOT called anywhere (the paid-API leaf is preserved end to end)
'''

if __name__ == '__main__':
    print('final combined output:')
    print(flow.outputLoadConcat(Sector))
