"""
# Utils
- - - - - - - - -
Different utility functions

:author: Rajendra.Adhikari@nrel.gov
"""
from functools import reduce
import yaml
import pandas as pd
import numpy as np
import logging
from itertools import combinations
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_COMBINATION_REPORT_COUNT = 5  # Don't print combination report; There would be 2^n - n - 1 rows


class UpgradesAnalyzer:
    def __init__(self, yaml_file, buildstock) -> None:
        self.yaml_file = yaml_file
        if isinstance(buildstock, str):
            self.buildstock_df = pd.read_csv(buildstock)
            self.buildstock_df.columns = [c.lower() for c in self.buildstock_df.columns]
            self.buildstock_df.rename(columns={'building': 'building_id'}, inplace=True)
            self.buildstock_df.set_index('building_id', inplace=True)
        elif isinstance(buildstock, pd.DataFrame):
            self.buildstock_df = buildstock
            if 'building_id' in self.buildstock_df.columns:
                self.buildstock_df.set_index('building_id', inplace=True)
        self.total_samples = len(self.buildstock_df)
        self.logic_cache = {}

    def read_cfg(self):
        with open(self.yaml_file) as f:
            cfg = yaml.load(f, Loader=yaml.SafeLoader)
        return cfg

    @staticmethod
    def _get_eq_str(condition):
        para, option = UpgradesAnalyzer._get_para_option(condition)
        return f"`{para.lower()}`=='{option}'"

    @staticmethod
    def _get_para_option(condition):
        try:
            para, option = condition.split('|')
        except ValueError:
            raise ValueError(f"Condition {condition} is invalid")
        return para.lower(), option

    @staticmethod
    def get_mentioned_parameters(logic):
        if not logic:
            return []

        if isinstance(logic, str):
            return [UpgradesAnalyzer._get_para_option(logic)[0]]
        elif isinstance(logic, list):
            all_params = []
            for el in logic:
                all_params.extend(UpgradesAnalyzer.get_mentioned_parameters(el))
            return list(dict.fromkeys(all_params))  # remove duplicates
        elif isinstance(logic, dict):
            return UpgradesAnalyzer.get_mentioned_parameters(list(logic.values())[0])
        else:
            raise ValueError("Invalid logic type")

    def print_unique_characteristic(self, upgrade_num, chng_type, base_bldg_list, compare_bldg_list):
        cfg = self.read_cfg()
        if upgrade_num == 0:
            raise ValueError(f"Upgrades are 1-indexed. Got {upgrade_num}")

        try:
            upgrade_cfg = cfg['upgrades'][upgrade_num - 1]
        except KeyError:
            raise ValueError(f"Invalid upgrade {upgrade_num}. Upgrades are 1-indexed, FYI.")
        parameter_list = []
        for option_cfg in upgrade_cfg['options']:
            parameter_list.append(UpgradesAnalyzer._get_para_option(option_cfg['option'])[0])
            parameter_list.extend(UpgradesAnalyzer.get_mentioned_parameters(option_cfg.get('apply_logic')))
        parameter_list = list(dict.fromkeys(parameter_list))
        res_df = self.buildstock_df
        compare_df = res_df.loc[compare_bldg_list]
        base_df = res_df.loc[base_bldg_list]
        print(f"Comparing {len(compare_df)} buildings with {len(base_df)} other buildings.")
        unique_vals_dict = dict()
        for col in res_df.columns:
            no_change_set = set(compare_df[col].fillna('').unique())
            other_set = set(base_df[col].fillna('').unique())
            only_in_no_change = no_change_set - other_set
            if only_in_no_change:
                print(f"Only {chng_type} buildings have {col} in {sorted(only_in_no_change)}")
                unique_vals_dict[(col,)] = set([(entry,) for entry in only_in_no_change])

        if not unique_vals_dict:
            print("No 1-column unique chracteristics found.")

        for combi_size in range(2, min(len(parameter_list) + 1, 5)):
            print(f"Checking {combi_size} column combinations out of {parameter_list}")
            found_uniq_chars = 0
            for cols in combinations(parameter_list, combi_size):
                compare_tups = compare_df[list(cols)].fillna('').drop_duplicates().itertuples(index=False, name=None)
                other_tups = base_df[list(cols)].fillna('').drop_duplicates().itertuples(index=False, name=None)
                only_in_compare = set(compare_tups) - set(other_tups)

                # remove cases arisen out of uniqueness found earlier with smaller susbset of cols
                for sub_combi_size in range(1, len(cols)):
                    for sub_cols in combinations(cols, sub_combi_size):
                        if sub_cols in unique_vals_dict.keys():
                            new_set = set()
                            for val in only_in_compare:
                                relevant_val = tuple([val[cols.index(sub_col)] for sub_col in sub_cols])
                                if relevant_val not in unique_vals_dict[sub_cols]:
                                    new_set.add(val)
                                else:
                                    pass  # drop this entry because it is coming from known uniqueness of subset of cols
                            only_in_compare = new_set

                if only_in_compare:
                    print(f"Only {chng_type} buildings have {cols} in {sorted(only_in_compare)} \n")
                    found_uniq_chars += 1
                    unique_vals_dict[cols] = only_in_compare

            if not found_uniq_chars:
                print(f"No {combi_size}-column unique chracteristics found.")
        return compare_df, base_df, parameter_list

    def reduce_logic(self, logic, parent=None):
        cache_key = str(logic) if parent is None else parent + "[" + str(logic) + "]"
        if cache_key in self.logic_cache:
            return self.logic_cache[cache_key]

        logic_array = np.ones((1, self.total_samples), dtype=bool)
        if parent not in [None, 'and', 'or', 'not']:
            raise ValueError(f"Logic can only inlcude and, or, not blocks. {parent} found in {logic}.")

        if isinstance(logic, str):
            para, opt = UpgradesAnalyzer._get_para_option(logic)
            logic_array = (self.buildstock_df[para] == opt)
        elif isinstance(logic, list):
            if len(logic) == 1:
                logic_array = self.reduce_logic(logic[0]).copy()
            elif parent in ['or']:
                logic_array = reduce(lambda l1, l2: l1 | self.reduce_logic(l2), logic,
                                     np.zeros((1, self.total_samples), dtype=bool))
            else:
                logic_array = reduce(lambda l1, l2: l1 & self.reduce_logic(l2), logic,
                                     np.ones((1, self.total_samples), dtype=bool))
        elif isinstance(logic, dict):
            if len(logic) > 1:
                raise ValueError(f"Dicts cannot have more than one keys. {logic} has.")
            key = list(logic.keys())[0]
            logic_array = self.reduce_logic(logic[key], parent=key).copy()

        if parent == 'not':
            return ~logic_array
        if not (isinstance(logic, str) or (isinstance(logic, list) and len(logic) == 1)):
            # Don't cache small logics - computing them again won't be too bad
            self.logic_cache[cache_key] = logic_array.copy()
        return logic_array

    def get_report(self):
        cfg = self.read_cfg()
        self.logic_cache = {}
        if 'upgrades' not in cfg:
            raise ValueError("The project yaml has no upgrades defined")
        records = []
        for indx, upgrade in enumerate(cfg['upgrades']):
            logger.info(f"Analyzing upgrade {indx + 1}")
            all_applied_bldgs = np.zeros((1, self.total_samples), dtype=bool)
            package_applied_bldgs = np.ones((1, self.total_samples), dtype=bool)
            if "package_apply_logic" in upgrade:
                package_flat_logic = UpgradesAnalyzer.flatten_lists(upgrade['package_apply_logic'])
                package_applied_bldgs = self.reduce_logic(package_flat_logic, parent=None)

            for opt_index, option in enumerate(upgrade['options']):
                if 'apply_logic' in option:
                    flat_logic = UpgradesAnalyzer.flatten_lists(option['apply_logic'])
                    applied_bldgs = self.reduce_logic(flat_logic, parent=None)
                else:
                    applied_bldgs = np.ones((1, self.total_samples), dtype=bool)

                applied_bldgs &= package_applied_bldgs
                count = applied_bldgs.sum()
                all_applied_bldgs |= applied_bldgs
                record = {'upgrade': str(indx+1), 'upgrade_name': upgrade['upgrade_name'],
                          'option_num': opt_index + 1,
                          'option': option['option'], 'applicable_to': count,
                          'applicable_percent': self.to_pct(count),
                          'cost': option.get('cost', 0),
                          'lifetime': option.get('lifetime', float('inf'))}
                records.append(record)

            count = all_applied_bldgs.sum()
            record = {'upgrade': str(indx+1), 'upgrade_name': upgrade['upgrade_name'],
                      'option_num': -1,
                      'option': "All", 'applicable_to': count,
                      'applicable_percent': self.to_pct(count)}
            records.append(record)
        report_df = pd.DataFrame.from_records(records)
        return report_df

    @staticmethod
    def flatten_lists(logic):
        if isinstance(logic, list):
            flat_list = []
            for el in logic:
                val = UpgradesAnalyzer.flatten_lists(el)
                if isinstance(val, list):
                    flat_list.extend(val)
                else:
                    flat_list.append(val)
            return flat_list
        elif isinstance(logic, dict):
            new_dict = {key: UpgradesAnalyzer.flatten_lists(value) for key, value in logic.items()}
            return new_dict
        else:
            return logic

    def _print_options_combination_report(self, logic_dict, comb_type='and'):
        n_options = len(logic_dict)
        assert comb_type in ['and', 'or']
        if n_options >= 2:
            header = f"Options '{comb_type}' combination report"
            print("-"*len(header))
            print(header)
        else:
            return
        for combination_size in range(2, n_options + 1):
            print("-"*len(header))
            for group in combinations(list(range(n_options)), combination_size):
                if comb_type == 'and':
                    combined_logic = reduce(lambda c1, c2: c1 & c2, [logic_dict[opt_indx] for opt_indx in group])
                else:
                    combined_logic = reduce(lambda c1, c2: c1 | c2, [logic_dict[opt_indx] for opt_indx in group])
                count = combined_logic.sum()
                text = f" {comb_type} ". join([f"Option {opt_indx + 1}" for opt_indx in group])
                print(f"{text}: {count} ({self.to_pct(count, len(combined_logic))}%)")
        print("-"*len(header))
        return

    def print_detailed_report(self, upgrade_num, option_num=None):
        cfg = self.read_cfg()
        self.logic_cache = {}
        if upgrade_num == 0 or option_num == 0:
            raise ValueError(f"Upgrades and options are 1-indexed.Got {upgrade_num} {option_num}")

        if option_num is None:
            conds_dict = {}
            n_options = len(cfg['upgrades'][upgrade_num - 1]['options'])
            or_array = np.zeros((1, self.total_samples), dtype=bool)
            and_array = np.ones((1, self.total_samples), dtype=bool)
            for option_indx in range(n_options):
                logic_array = self.print_detailed_report(upgrade_num, option_indx + 1)
                if n_options <= MAX_COMBINATION_REPORT_COUNT:
                    conds_dict[option_indx] = logic_array
                or_array |= logic_array
                and_array &= logic_array
            and_count = and_array.sum()
            or_count = or_array.sum()
            if n_options <= MAX_COMBINATION_REPORT_COUNT:
                self._print_options_combination_report(conds_dict, 'and')
                self._print_options_combination_report(conds_dict, 'or')
            else:
                text = f"Combination report not printed because {n_options} options would require "\
                       f"{2**n_options - n_options - 1} rows."
                print(text)
                print("-"*len(text))
            print(f"All of the options (and-ing) were applied to: {and_count} ({self.to_pct(and_count)}%)")
            print(f"Any of the options (or-ing) were applied to: {or_count} ({self.to_pct(or_count)}%)")
            return

        try:
            upgrade = cfg['upgrades'][upgrade_num - 1]
            opt = upgrade['options'][option_num - 1]
        except (KeyError, IndexError, TypeError):
            raise ValueError(f"The yaml doesn't have {upgrade_num}/{option_num} upgrade/option")

        ugrade_name = upgrade.get('upgrade_name')
        header = f"Option Apply Report for - Upgrade{upgrade_num}:'{ugrade_name}', Option{option_num}:'{opt['option']}'"
        print("-"*len(header))
        print(header)
        print("-"*len(header))
        if "apply_logic" in opt:
            logic = UpgradesAnalyzer.flatten_lists(opt['apply_logic'])
            logic_array, logic_str = self._get_logic_report(logic)
            footer_len = len(logic_str[-1])
            print("\n".join(logic_str))
            print("-"*footer_len)
        else:
            logic_array = np.ones((1, self.total_samples), dtype=bool)

        if "package_apply_logic" in upgrade:
            logic = UpgradesAnalyzer.flatten_lists(upgrade['package_apply_logic'])
            package_logic_array, logic_str = self._get_logic_report(logic)
            footer_len = len(logic_str[-1])
            print("Package Apply Logic Report")
            print("--------------------------")
            print("\n".join(logic_str))
            print("-"*footer_len)
            logic_array = logic_array & package_logic_array

        count = logic_array.sum()
        footer_str = f"Overall applied to => {count} ({self.to_pct(count)}%)."
        print(footer_str)
        print('-'*len(footer_str))
        return logic_array

    def to_pct(self, count, total=None):
        total = total or self.total_samples
        return round(100 * count / total, 1)

    def _get_logic_report(self, logic, parent=None):
        logic_array = np.ones((1, self.total_samples), dtype=bool)
        logic_str = ['']
        if parent not in [None, 'and', 'or', 'not']:
            raise ValueError(f"Logic can only inlcude and, or, not blocks. {parent} found in {logic}.")
        if isinstance(logic, str):
            logic_condition = UpgradesAnalyzer._get_eq_str(logic)
            logic_array = self.buildstock_df.eval(logic_condition, engine='python')
            count = logic_array.sum()
            logic_str = [logic + " => " + f"{count} ({self.to_pct(count)}%)"]
        elif isinstance(logic, list):
            if len(logic) == 1:
                logic_array, logic_str = self._get_logic_report(logic[0])
            elif parent in ['or']:
                def reducer(l1, l2):
                    ll2 = self._get_logic_report(l2)
                    return l1[0] | ll2[0], l1[1] + ll2[1]
                logic_array, logic_str = reduce(reducer, logic,
                                                (np.zeros((1, self.total_samples), dtype=bool), []))
            else:
                def reducer(l1, l2):
                    ll2 = self._get_logic_report(l2)
                    return l1[0] & ll2[0], l1[1] + ll2[1]
                logic_array, logic_str = reduce(reducer, logic,
                                                (np.ones((1, self.total_samples), dtype=bool), []))
        elif isinstance(logic, dict):
            if len(logic) > 1:
                raise ValueError(f"Dicts cannot have more than one keys. {logic} has.")
            key = list(logic.keys())[0]
            sub_logic = self._get_logic_report(logic[key], parent=key)
            sub_logic_str = sub_logic[1]
            logic_array = sub_logic[0]
            if key == 'not':
                logic_array = ~logic_array
            count = logic_array.sum()
            header_str = key + " => " + f"{count} ({self.to_pct(count)}%)"
            logic_str = [header_str] + [f"  {ls}" for ls in sub_logic_str]

        count = logic_array.sum()
        if parent is None and isinstance(logic, list) and len(logic) > 1:
            logic_str[0] = logic_str[0] + " => " + f"{count} ({self.to_pct(count)}%)"

        return logic_array, logic_str
