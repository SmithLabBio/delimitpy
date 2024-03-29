"""This module contains all Classes for creating msprime demographies to be used in downstream simulation."""

import dendropy # ModelBuilder
import pandas as pd # ModelBuilder

import msprime # ModelBuilder
import copy # ModelBuilder
from itertools import chain, combinations # ModelBuilder
import numpy as np # ModelBuilder
import demes # ModelBuilder
import random # ModelBuilder only used for demes plotting
import demesdraw # ModelBuilder
import matplotlib.pyplot as plt # ModelBuilder

import logging # ModelBuilder, ModelReader

import yaml # ModelWriter

import os # ModelReader



class ModelBuilder:

    """Generate a model set with parameters drawn from user-defined priors."""

    def __init__(self, config_values):
        self.config = config_values
        self.rng = np.random.default_rng(self.config['seed'])

        # Configure logging
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)

    def build_models(self):
        """
        Build msprime models for divergence, secondary contact, and divergence with gene flow scenarios.

        Parameters:
            species tree: A dendropy species tree object with annotations specifying distributions on population sizes and divergence times.

        Returns:
            a list of base msprime demographies with divergence only (without accurate divergence times and population sizes.)

        Raises:
            Error: If baseline demographies cannot be created.
        """
        try:
            # get baseline divergence demographies
            divergence_demographies = self._create_divergence_demographies()

            # get secondary contact demographies
            if self.config['secondary contact']:
                sc_demographies = self._create_sc_demographies(divergence_demographies)
            else:
                sc_demographies = []

            # get divergence with gene flow demographies
            if self.config['divergence with gene flow']:
                dwg_demographies = self._create_dwg_demographies(divergence_demographies)
            else:
                dwg_demographies = []

            total_models = len(divergence_demographies) + len(sc_demographies) + len(dwg_demographies)
            self.logger.info(f"Creating {total_models} different models based on user input.")

            self.divergence_demographies = divergence_demographies
            self.sc_demographies = sc_demographies
            self.dwg_demographies = dwg_demographies

        except ValueError as ve:
            raise ValueError(f"ValueError: Issue when building baseline msprime demographies: {ve}")

        except Exception as e:
            raise Exception(f"Error: Issue when building baseline msprime demographies: {e}")

    def draw_parameters(self):
        """
        Draw parameters for all models.

        Parameters:
            divergence_demographies (List): A list of divergence demography objects returned from build_models.
            sc_demographies (List): A list of secondary contact demography objects returned from build_models.
            dwg_demographies (List): A list of divergence with gene flow demography objects returned from build_models.

        Returns:
            List: a list of base demographies with divergence only (without accurate divergence times and population sizes.)

        Raises:
            Error if priors are incorrectly defined.
        """
        # get priors
        population_sizes, divergence_times = _get_priors(self.config)

        # draw parameters for divergence models
        parameterized_divergence_demographies = self._draw_parameters_divergence(divergence_times=divergence_times, population_sizes=population_sizes)

        # draw parameters for secondary contact models
        parameterized_sc_demographies = self._draw_parameters_sc(divergence_times=divergence_times, population_sizes=population_sizes)

        # draw parameters for divergence with gene flow models
        parameterized_dwg_demographies = self._draw_parameters_dwg(divergence_times=divergence_times, population_sizes=population_sizes)

        # flatten models and create labels
        full_model_list = parameterized_divergence_demographies+parameterized_sc_demographies+parameterized_dwg_demographies
        labels = list(range(len(full_model_list)))
        expanded_labels = [[val]*len(sublist) for val, sublist in zip(labels, full_model_list)]

        # return them
        return(full_model_list, expanded_labels)

    def validate_models(self, demographies, labels):
        """
        Plot example models from divergence, secondary contact, and divergence with gene flow scenarios.

        Parameters:
            divergence_demographies (List): A list of parameterized divergence demography objects returned from draw_parameters.
            sc_demographies (List): A list of parameterized secondary contact demography objects returned from draw_parameters.
            dwg_demographies (List): A list of parameterized divergence with gene flow demography objects returned from draw_parameters.

        Returns:
            Nothing

        Raises:
            Error: If models cannot be plotted.
        """

        try:
            # Plot divergence demographies
            self._plot_models(demographies, labels)


        except ValueError as ve:
            raise ValueError(f"ValueError: Issue when plotting example msprime demographies: {ve}")

        except Exception as e:
            raise Exception(f"Error: Issue when plotting example msprime demographies: {e}")

    def _create_divergence_demographies(self):
        """Create baseline divergence demographies."""

        demographies = []
        pop_size_holder = 1000
        div_time_holder = 1000

        # get a list of collapsable nodes, and then get all potential combos of these
        collapsable_nodes = self.config['species tree'].internal_nodes()
        all_combos = [list(combo) for r in range(1, len(collapsable_nodes) + 1) for combo in combinations(collapsable_nodes, r)]

        # remove conflicting combos, to get a list of the models to include (minus the full model)
        all_combos = self._remove_conflicting(all_combos)
        all_combos.append([])

        # now generate the demographies for all combos
        for combo in all_combos:
            demography = msprime.Demography()

            # add populations that should be added, and set initial sizes (these will be changed later.)
            for internal_node in self.config['species tree'].postorder_internal_node_iter():
                if internal_node not in combo:
                    derived_1, derived_2 = self._get_derived_populations(internal_node)
                    demography.add_population(name=derived_1, initial_size=pop_size_holder)
                    demography.add_population(name=derived_2, initial_size=pop_size_holder)
            demography.add_population(name=str(self.config['species tree'].seed_node.label).strip("'"), initial_size=pop_size_holder)

            # add non-zero divergence events
            for internal_node in self.config['species tree'].postorder_internal_node_iter():
                if internal_node not in combo:
                    derived_1, derived_2 = self._get_derived_populations(internal_node)
                    ancestral = str(internal_node.label).strip("'")
                    demography.add_population_split(time=div_time_holder, derived=[derived_1, derived_2], ancestral=ancestral)

            demographies.append(demography)

        return(demographies)

    def _create_sc_demographies(self, divergence_demographies):
        """Create baseline secondary contact demographies."""

        migration_demographies = []
        pop_size_holder = 1000
        div_time_holder = 1000
        migtimeholder = 100
        migrateholder = 1e-3

        # iterate over demographies
        for item in divergence_demographies:

            # list of events to include
            to_include_base = sorted(set(self._find_sc_to_include(item)))

            # keep only one per pair if symmetric rates are enforced
            if self.config['symmetric']:
                to_include_frozensets = []
                for pair in to_include_base:
                    sorted_pair = sorted(pair)
                    to_include_frozensets.append(tuple(sorted_pair))

                to_include = sorted(set(to_include_frozensets))

            # get all combos of events
            combos_of_migration = chain.from_iterable(combinations(to_include, r) for r in range(1, min(self.config['max migration events'],len(to_include)) + 1))
            combos_of_migration = [list(combo) for combo in combos_of_migration]

            # create histories with each combo of events
            for combo in combos_of_migration:
                migration_demography = copy.deepcopy(item)

                for populationpair in combo:
                    populationpair=list(populationpair)

                    # if symmetric true, then add symmetric migration and add ceasing of migration
                    if self.config['symmetric']:
                        migration_demography.set_symmetric_migration_rate(populationpair, migrateholder)
                        migration_demography.add_symmetric_migration_rate_change(migtimeholder, populationpair, 0)

                    # if not symmetric then do asymmetric
                    else:
                        migration_demography.set_migration_rate(source=populationpair[0], dest=populationpair[1], rate=migrateholder)
                        migration_demography.add_migration_rate_change(time=migtimeholder, source=populationpair[0], dest=populationpair[1], rate=0)

                # sort the events and add the demography to the list
                migration_demography.sort_events()
                migration_demographies.append(migration_demography)

        return(migration_demographies)

    def _create_dwg_demographies(self, divergence_demographies):
        """Create baseline secondary contact demographies."""

        migration_demographies = []
        pop_size_holder = 1000
        div_time_holder = 1000
        migtimeholder = 100
        migrateholder = 1e-3

        # iterate over demographies
        for item in divergence_demographies:

            # list of events to include
            to_include_base = sorted(set(self._find_dwg_to_include(item)))

            # keep only one per pair if symmetric rates are enforced
            if self.config['symmetric']:
                to_include_frozensets = []
                for pair in to_include_base:
                    sorted_pair = sorted(pair)
                    to_include_frozensets.append(tuple(sorted_pair))

                to_include = sorted(set(to_include_frozensets))

            # get all combos of events
            combos_of_migration = chain.from_iterable(combinations(to_include, r) for r in range(1, min(self.config['max migration events'],len(to_include)) + 1))
            combos_of_migration = [list(combo) for combo in combos_of_migration]

            # create histories with each combo of events
            for combo in combos_of_migration:
                migration_demography = copy.deepcopy(item)

                for populationpair in combo:
                    populationpair=list(populationpair)
                    # if symmetric true, then add symmetric migration and add ceasing of migration
                    if self.config['symmetric']:
                        migration_demography.add_symmetric_migration_rate_change(migtimeholder, populationpair, rate=migrateholder)
                    # if not symmetric then do asymmetric
                    else:
                        migration_demography.add_migration_rate_change(time=migtimeholder, source=populationpair[0], dest=populationpair[1], rate=migrateholder)

                # sort the events and add the demography to the list
                migration_demography.sort_events()
                migration_demographies.append(migration_demography)
                del(migration_demography)


        return(migration_demographies)

    def _remove_conflicting(self, all_combos):
        """Remove any combos of nodes to collapse that are conflicting, meaning that daughter nodes of collapsed nodes are not collapsed."""
        keep_combos = []

        for combo in all_combos:

            collapsed_nodes = set(combo)

            for node in combo:
                child_nodes = [child for child in node.postorder_internal_node_iter() if child not in combo]
            if len(child_nodes)==0:
                keep_combos.append(combo)

        return(keep_combos)

    def _get_derived_populations(self, internal_node):
        """Get the names of populations descending from an internal node."""

        children = internal_node.child_nodes()

        derived_1, derived_2 = [x.label if x.label is not None else x.taxon.label for x in children]

        return(derived_1, derived_2)

    def _find_sc_to_include(self, item):
        """Find which secondary contact events we whould include for the demography, 'item'"""
        to_include = []

        # iterate over migration matrix and find populations with migration
        for index, row in self.config['migration_df'].iterrows():
            for colname, value in row.items():
                include = [False,False]

                if index != colname and value =="T":

                    # check that the population diverged from its ancestor at a time > 0
                    for population in enumerate([index, colname]):
                        for event in item.events:
                            if population[1] in event.derived and event.time != 0:
                                include[population[0]] = True
                        for event in item.events:
                            if population[1] == event.ancestral and event.time != 0:
                                include[population[0]] = False

                # if both poulations meet are criteria, include the event.
                if include[0] and include[1]:
                    to_include.append((index,colname))

        return(to_include)

    def _find_dwg_to_include(self, item):
        """Find which divergence with gene flow events we whould include for the demography, 'item'"""
        to_include = []

        # iterate over migration matrix and find populations with migration
        for index, row in self.config['migration_df'].iterrows():
            for colname, value in row.items():
                include = False

                if index != colname and value =="T":

                    # check that the population diverged from its ancestor at a time > 0
                    for event in item.events:
                        if index in event.derived and colname in event.derived and event.time != 0:
                            include = True

                # if both poulations meet are criteria, include the event.
                if include:
                    to_include.append((index,colname))

        return(to_include)

    def _draw_parameters_divergence(self, divergence_times, population_sizes):
        """Draw parameters for divergence models."""
 
        models_with_parameters = []

        for original_model in self.divergence_demographies:

            this_model_with_parameters = []

            population_size_draws, population_size_keys = self._draw_population_sizes(original_model, population_sizes)
            divergence_time_draws = self._draw_divergence_times(population_size_draws, original_model, divergence_times)

            for rep in range(self.config['replicates']):
                model = copy.deepcopy(original_model)
                for population in model.populations:
                    population.initial_size = population_size_draws[population.name][rep]
                for event in model.events:
                    if hasattr(event, 'ancestral'):
                        event.time = divergence_time_draws[event.ancestral][rep]

                this_model_with_parameters.append(model)

            models_with_parameters.append(this_model_with_parameters)

        return(models_with_parameters)

    def _draw_parameters_sc(self, divergence_times, population_sizes):
        """Draw parameters for secondary contact models."""

        models_with_parameters = []

        for original_model in self.sc_demographies:

            this_model_with_parameters = []

            population_size_draws, population_size_keys = self._draw_population_sizes(original_model, population_sizes)
            divergence_time_draws = self._draw_divergence_times(population_size_draws, original_model, divergence_times)
            migration_rate_draws = self._draw_migration_rates(population_size_keys, original_model)
            migration_stop = self._get_migration_stops(divergence_time_draws)
    
            for rep in range(self.config['replicates']):
                model = copy.deepcopy(original_model)
                for population in model.populations:
                    population.initial_size = population_size_draws[population.name][rep]
                for event in model.events:
                    if hasattr(event, 'ancestral'):
                        event.time = divergence_time_draws[event.ancestral][rep]
                    elif hasattr(event, 'rate'):
                        event.time = migration_stop[rep]
                for key in migration_rate_draws.keys():
                    model.migration_matrix[int(str(key.split('_')[0])),int(str(key.split('_')[1]))] = migration_rate_draws[key][rep]
                    if self.config["symmetric"]:
                        model.migration_matrix[int(str(key.split('_')[1])),int(str(key.split('_')[0]))] = migration_rate_draws[key][rep]
                this_model_with_parameters.append(model)

            models_with_parameters.append(this_model_with_parameters)   #
        return(models_with_parameters)

    def _draw_parameters_dwg(self, divergence_times, population_sizes):
        """Draw parameters for divergence with gene flow models."""

        models_with_parameters = []

        for original_model in self.dwg_demographies:

            this_model_with_parameters = []

            population_size_draws, population_size_keys = self._draw_population_sizes(original_model, population_sizes)
            divergence_time_draws = self._draw_divergence_times(population_size_draws, original_model, divergence_times)
            migration_rate_draws = self._draw_migration_rates(population_size_keys, original_model)
            migration_start = self._get_migration_starts(original_model, divergence_time_draws, population_size_keys)

            for rep in range(self.config['replicates']):
                model = copy.deepcopy(original_model)
                for population in model.populations:
                    population.initial_size = population_size_draws[population.name][rep]
                for event in model.events:
                    if hasattr(event, 'ancestral'):
                        event.time = divergence_time_draws[event.ancestral][rep]
                    elif hasattr(event, 'rate'):
                        if self.config['symmetric']:
                            event.time = migration_start["%s_%s" % (population_size_keys[event.populations[0]], population_size_keys[event.populations[1]])][rep]
                            event.rate = migration_rate_draws["%s_%s" % (population_size_keys[event.populations[0]], population_size_keys[event.populations[1]])][rep]
                        else:
                            event.time = migration_start["%s_%s" % (population_size_keys[event.source], population_size_keys[event.dest])][rep]
                            event.rate = migration_rate_draws["%s_%s" % (population_size_keys[event.source], population_size_keys[event.dest])][rep]

                model.sort_events()
                this_model_with_parameters.append(model)

            models_with_parameters.append(this_model_with_parameters)

        return(models_with_parameters)

    def _draw_population_sizes(self, model, population_sizes):
        """Draw population sizes from priors."""

        population_size_draws = {}
        population_size_keys = {}

        for index, population in enumerate(model.populations):
            min_size, max_size = population_sizes[population.name]
            population_size_draws[population.name] = np.round(self.rng.uniform(low=min_size, high=max_size, size=self.config['replicates']),0)
            population_size_keys[population.name] = index

        return(population_size_draws, population_size_keys)

    def _draw_divergence_times(self, population_size_draws, model, divergence_times):
        """Draw divergence times from priors."""

        divergence_time_draws = {}

        # create a list of populations that are never ancestors
        all_populations = population_size_draws.keys()
        ancestral = [x for x in model.events if hasattr(x, 'ancestral')]
        ancestral = [x.ancestral for x in ancestral]
        non_ancestral = list(set(all_populations)- set(ancestral))

        # add zero for non-ancestral populations
        for x in non_ancestral:
            divergence_time_draws[x] = np.repeat(0, self.config['replicates'])

        for event in model.events:
            if hasattr(event, 'ancestral'):
                if event.time != 0:
                    # get the divergence times of the derived populations, and use that to set the minimum value for drawing divergence times
                    min_values = [max(x) for x in zip(divergence_time_draws[event.derived[0]], divergence_time_draws[event.derived[1]], np.repeat(divergence_times[event.ancestral][0], self.config['replicates']))]
                    divergence_time_draws[event.ancestral] = [np.round(self.rng.uniform(low=x, high=divergence_times[event.ancestral][1]),0) for x in min_values]
                elif event.time == 0:
                    divergence_time_draws[event.ancestral] = np.repeat(0, self.config['replicates'])

        return(divergence_time_draws)

    def _draw_migration_rates(self, population_size_keys, model):
        """Draw migration rates from priors."""

        migration_rate_draws = {}

        for event in model.events:
            if hasattr(event, 'rate'):
                if self.config['symmetric']:
                    migration_rate_draws["%s_%s" % (population_size_keys[event.populations[0]], population_size_keys[event.populations[1]])] = np.round(self.rng.uniform(low=self.config["migration_rate"][0], high=self.config["migration_rate"][1], size=self.config["replicates"]),10)
                else:
                    migration_rate_draws["%s_%s" % (population_size_keys[event.source], population_size_keys[event.dest])] = np.round(self.rng.uniform(low=self.config["migration_rate"][0], high=self.config["migration_rate"][1], size=self.config["replicates"]),10)
        return(migration_rate_draws)

    def _get_migration_stops(self, divergence_time_draws):
        """Get stop times for migration in the secondary contact models."""

        minimum_divergence = []
        for rep in range(self.config["replicates"]):
            min = np.inf
            for key in divergence_time_draws:
                if divergence_time_draws[key][rep] > 0 and divergence_time_draws[key][rep] < min:
                    min = divergence_time_draws[key][rep]
            minimum_divergence.append(min)
        migration_stop = [np.ceil(x/2) for x in minimum_divergence]
        return(migration_stop)

    def _get_migration_starts(self, model, divergence_time_draws, population_size_keys):
        """Get start times for migration in the divergence with gene flow models."""

        migration_start = {}

        for rep in range(self.config['replicates']):

            for event in model.events:

                if hasattr(event, 'rate'):
                    if self.config['symmetric']:
                        daughter1, daughter2 = event.populations
                    else:
                        daughter1, daughter2 = event.source, event.dest

                    for divevent in model.events:
                        if hasattr(divevent, 'ancestral'):
                            if daughter1 in divevent.derived and daughter2 in divevent.derived:
                                ancestor = divevent.ancestral

                    tdiv_ancestor = divergence_time_draws[ancestor][rep]
                    tdiv_daughter1 = divergence_time_draws[daughter1][rep]
                    tdiv_daughter2 = divergence_time_draws[daughter2][rep]
                    startime = ((tdiv_ancestor-max(tdiv_daughter1, tdiv_daughter2))/2) + max(tdiv_daughter1, tdiv_daughter2)

                    try:
                        migration_start["%s_%s" % (population_size_keys[daughter1], population_size_keys[daughter2])].append(startime)
                    except:
                        migration_start["%s_%s" % (population_size_keys[daughter1], population_size_keys[daughter2])] = []
                        migration_start["%s_%s" % (population_size_keys[daughter1], population_size_keys[daughter2])].append(startime)

        return(migration_start)

    def _plot_models(self, demographies, labels):
        """Plot example models for a given type of demography."""

        for model in enumerate(demographies):
            demo_to_plot = random.sample(model[1], 1)[0]
            graph = demo_to_plot.to_demes()

            # Plot the model
            fig, ax = plt.subplots()
            demesdraw.tubes(graph, ax=ax, seed=1)
            plt.title('Model: %s' % labels[model[0]][0])
            plt.show()

class ModelWriter:

    """Write models generated by delimitpy to a file."""

    def __init__(self, config, divergence_demographies, sc_demographies, dwg_demographies):
        self.config = config
        self.divergence_demographies = divergence_demographies
        self.sc_demographies = sc_demographies
        self.dwg_demographies = dwg_demographies

    def write_to_yaml(self):
        """
        Writes delimitpy generated models to a yaml file, which can later be read by delimitpy.

        Parameters:
            config_dict Dict: dictionary crated by ModelConfigParser function parse_config.
            divergence_demographies List of Lists: Parameterized divergence demographies generated by ModelBuilder function draw_parameters.
            sc_demographies List of Lists: Parameterized secondary contact demographies generated by ModelBuilder function draw_parameters.
            dwg_demographies List of Lists: Parameterized divergence with gene flow demographies generated by ModelBuilder function draw_parameters.

        Outputs:
            yaml files for each model (not for each parameterization)

        Returns:

        Raises:
        """

        self._divergence_to_yaml()
        self._sc_to_yaml(start=len(self.divergence_demographies))
        self._dwg_to_yaml(start=len(self.divergence_demographies)+len(self.sc_demographies))

    def _divergence_to_yaml(self):

        # get population size and divergence time priors
        population_sizes, divergence_times = _get_priors(self.config)

        for model in enumerate(self.divergence_demographies):

            # create a dict for the yaml file
            yaml_dict = {}

            # add description
            yaml_dict = self._get_description(model, yaml_dict, 'Divergence')

            # write population information to dictionary
            yaml_dict = self._get_popinfo(model, yaml_dict, population_sizes)

            # write divergence information to dictionary
            yaml_dict = self._get_divinfo(model, yaml_dict, divergence_times)

            # write yaml to file
            f = open('%s/Model_%s.yaml' % (self.config['output directory'], model[0]), 'w')
            yaml.dump(yaml_dict, f)
            f.close()

    def _sc_to_yaml(self, start):

        # get population size and divergence time priors
        population_sizes, divergence_times = _get_priors(self.config)

        for model in enumerate(self.sc_demographies):

            # create a dict for the yaml file
            yaml_dict = {}

            # add description
            yaml_dict = self._get_description(model, yaml_dict, 'Secondary Contact', start=start)

            # write population information to dictionary
            yaml_dict = self._get_popinfo(model, yaml_dict, population_sizes)

            # write divergence information to dictionary
            yaml_dict = self._get_divinfo(model, yaml_dict, divergence_times)

            # write migration events
            yaml_dict = self._get_scinfo(model, yaml_dict)

            # write yaml to file
            f = open('%s/Model_%s.yaml' % (self.config['output directory'], model[0]+start), 'w')
            yaml.dump(yaml_dict, f, Dumper=NoAliasDumper)
            f.close()

    def _dwg_to_yaml(self, start):

        # get population size and divergence time priors
        population_sizes, divergence_times = _get_priors(self.config)

        for model in enumerate(self.dwg_demographies):

            # create a dict for the yaml file
            yaml_dict = {}

            # add description
            yaml_dict = self._get_description(model, yaml_dict, 'Divergence with gene flow', start=start)

            # write population information to dictionary
            yaml_dict = self._get_popinfo(model, yaml_dict, population_sizes)

            # write divergence information to dictionary
            yaml_dict = self._get_divinfo(model, yaml_dict, divergence_times)

            # write migration events
            yaml_dict = self._get_dwginfo(model, yaml_dict)

            # write yaml to file
            f = open('%s/Model_%s.yaml' % (self.config['output directory'], model[0]+start), 'w')
            yaml.dump(yaml_dict, f, Dumper=NoAliasDumper)
            f.close()

    def _get_description(self, model, yaml_dict, type, start=0):
        # get number of populations in the present and write description
        npops_present = int((len(model[1][0].populations)+1)/2)
        description = "Model %s: %s with %s populations." % (model[0]+start, type, npops_present)
        yaml_dict['description'] = description
        return(yaml_dict)

    def _get_popinfo(self, model, yaml_dict, population_sizes):
        yaml_dict['populations'] = []
        yaml_dict['priors'] = {}
        for population in enumerate(model[1][0].populations):
            yaml_dict['populations'].append({})
            yaml_dict['populations'][population[0]]['name'] = population[1].name
            yaml_dict['populations'][population[0]]['size'] = 'Ne_' + population[1].name
            yaml_dict['priors']['Ne_'+ population[1].name] = population_sizes[population[1].name]
        return(yaml_dict)

    def _get_divinfo(self, model, yaml_dict, divergence_times):
        yaml_dict['divergences'] = []
        count=0
        for event in model[1][0].events:
            if hasattr(event, 'ancestral'):
                yaml_dict['divergences'].append({})
                yaml_dict['divergences'][count]['derived'] = event.derived
                yaml_dict['divergences'][count]['ancestral'] = event.ancestral
                yaml_dict['divergences'][count]['time'] = 'Tdiv_'+event.ancestral
                yaml_dict['priors']['Tdiv_'+event.ancestral] = divergence_times[event.ancestral]
                count+=1
        return(yaml_dict)
    
    def _get_scinfo(self, model, yaml_dict):
        yaml_dict['migration'] = []
        yaml_dict['relations'] = {}
        count=0
        for event in model[1][0].events:
            if hasattr(event, "rate"):
                yaml_dict['migration'].append({})
                if hasattr(event, "populations"):
                    yaml_dict['migration'][count]['type'] = 'symmetric'
                    pop0 = event.populations[0]
                    pop1 = event.populations[1]
                else:
                    yaml_dict['migration'][count]['type'] = 'asymmetric'
                    pop0 = event.source
                    pop1 = event.dest

                yaml_dict['migration'][count]['source'] = pop0
                yaml_dict['migration'][count]['dest'] = pop1
                yaml_dict['migration'][count]['rate'] = 'Mig_%s_%s' % (pop0, pop1)
                yaml_dict['migration'][count]['start_time'] = 0
                yaml_dict['migration'][count]['end_time'] = 'Tmigend_%s_%s' % (pop0, pop1)
                yaml_dict['priors']['Mig_%s_%s' % (pop0, pop1)] = self.config['migration_rate']
                time_values = [entry['time'] for entry in yaml_dict['divergences']]
                time_values = 'min('+','.join(time_values)+')/2'
                yaml_dict["relations"]['Tmigend_%s_%s' % (pop0, pop1)] = time_values
                count+=1
        return(yaml_dict)

    def _get_dwginfo(self, model, yaml_dict):
        yaml_dict['migration'] = []
        yaml_dict['relations'] = {}
        count=0
        for event in model[1][0].events:
            if hasattr(event, "rate"):
                yaml_dict['migration'].append({})
                if hasattr(event, "populations"):
                    yaml_dict['migration'][count]['type'] = 'symmetric'
                    pop0 = event.populations[0]
                    pop1 = event.populations[1]
                else:
                    yaml_dict['migration'][count]['type'] = 'asymmetric'
                    pop0 = event.source
                    pop1 = event.dest

                yaml_dict['migration'][count]['source'] = pop0
                yaml_dict['migration'][count]['dest'] = pop1
                yaml_dict['migration'][count]['rate'] = 'Mig_%s_%s' % (pop0, pop1)
                yaml_dict['migration'][count]['start_time'] = 'Tmigstart_%s_%s' % (pop0, pop1)
                yaml_dict['priors']['Mig_%s_%s' % (pop0, pop1)] = self.config['migration_rate']

                # get migration start times
                pop0div = 0
                pop1div = 0
                for event in model[1][0].events:
                    if hasattr(event, 'ancestral'):
                        if pop0 in event.derived and pop1 in event.derived:
                            ancestraldiv = event.ancestral
                        if event.ancestral == pop0:
                            pop0div = event.ancestral
                        if event.ancestral == pop1:
                            pop1div = event.ancestral
                if pop0div != 0 and pop1div != 0:
                    time_value = '(Tdiv_%s - min(Tdiv_%s, Tdiv_%s))/2 + min(Tdiv_%s, Tdiv_%s)' % (ancestraldiv, pop0div, pop1div, pop0div, pop1div)
                elif pop0div != 0:
                    time_value = '(Tdiv_%s - Tdiv_%s)/2 + Tdiv_%s' % (ancestraldiv, pop0div, pop0div)
                elif pop1div != 0:
                    time_value = '(Tdiv_%s - Tdiv_%s)/2 + Tdiv_%s' % (ancestraldiv, pop1div, pop1div)
                else:
                    time_value = 'Tdiv_%s/2' % (ancestraldiv)
                yaml_dict["relations"]['Tmigstart_%s_%s' % (pop0, pop1)] = time_value
                
                count+=1
        return(yaml_dict)

class ModelReader:

    """Read models from .yaml file."""

    def __init__(self, path_to_yaml, replicates, seed):
        self.path_to_yaml = path_to_yaml
        self.replicates = replicates
        self.seed = seed
        
        # Configure logging
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)

        # RNG
        self.rng = np.random.default_rng(self.seed)

    def read_yaml(self):

        # list the yaml files in the directory
        yaml_files = os.listdir(self.path_to_yaml)
        yaml_files = [x for x in yaml_files if x.endswith('.yaml')]
        
        # log the information
        self.logger.info(f"Reading {len(yaml_files)} models from yaml files.")

        for i in enumerate(yaml_files):

            with open(os.path.join(self.path_to_yaml, i[1]), 'r') as f:
                yaml_info = yaml.safe_load(f)

            # draw the population sizes from priors
            population_size_draws = {}
            for population in yaml_info['populations']:
                sizeprior = yaml_info['priors'][population['size']]
                population_size_draws[population['name']] = np.round(self.rng.uniform(low=sizeprior[0], high=sizeprior[1], size=self.replicates),0)

            # find dependencies between divergence times
            divergence_dependencies = {}
            for divergence in yaml_info['divergences']:
                divergence_dependencies[divergence['ancestral']] = None
                for divergence2 in yaml_info['divergences']:
                    if divergence2['ancestral'] in divergence['derived']:
                        divergence_dependencies[divergence['ancestral']] = divergence2['ancestral']

            # ensure divergence times are listed in order
            divergence_list_ordered = []
            added = []
            while len(divergence_list_ordered) < len(yaml_info['divergences']):
                for divergence in yaml_info['divergences']:
                    if divergence_dependencies[divergence['ancestral']] is None and divergence['ancestral'] not in added:
                        divergence_list_ordered.append(divergence)
                        added.append(divergence['ancestral'])
                    elif divergence_dependencies[divergence['ancestral']] in added and divergence['ancestral'] not in added:
                        divergence_list_ordered.append(divergence)
                        added.append(divergence['ancestral'])

            # draw divergence times from priors
            divergence_time_draws = {}
            for divergence in divergence_list_ordered:
                divprior = yaml_info['priors']['Tdiv_%s' % divergence['ancestral']]
                if divergence_dependencies[divergence['ancestral']] is None:
                    divergence_time_draws[divergence['ancestral']] = np.round(self.rng.uniform(low=divprior[0], high=divprior[1], size=self.replicates),0)
                else:
                    if divergence['derived'][0] in divergence_time_draws and divergence['derived'][1] in divergence_time_draws:
                        min_values = [max(x) for x in zip(divergence_time_draws[divergence['derived'][0]], divergence_time_draws[divergence['derived'][1]], np.repeat(divprior[0], self.replicates))]
                    elif divergence['derived'][0] in divergence_time_draws:
                        min_values = [max(x) for x in zip(divergence_time_draws[divergence['derived'][0]], np.repeat(divprior[0], self.replicates))]
                    elif divergence['derived'][1] in divergence_time_draws:
                        min_values = [max(x) for x in zip(divergence_time_draws[divergence['derived'][1]], np.repeat(divprior[0], self.replicates))]

                    divergence_time_draws[divergence['ancestral']] = [np.round(self.rng.uniform(low=x, high=divprior[1]),0) for x in min_values]

            # draw migration rates from priors and get times
            # STOPPED HERE SATURDAY
            migration_rates = {}
            migration_times = {}
            if 'migration' in yaml_info :
                for migration in yaml_info['migration']:
                    migprior = yaml_info['priors'][migration['rate']]
                    migration_rates[migration['rate']] = np.round(self.rng.uniform(low=migprior[0], high=migprior[1], size=self.replicates),10)

                    if 'end_time' in migration:
                        endtime=yaml_info['relations']['Tmigend_%s_%s' % (migration['source'], migration['dest'])]
                        mintimes = endtime.split('(')[1].split(')')[0].split(',')
                        


                    

def _get_priors(config):
    """Get priors for population sizes and divergence times from the species tree."""
    # build dictionary of priors for population sizes
    population_sizes = {}
    divergence_times = {}

    # get priors from species tree
    try:
        for node in config['species tree'].postorder_node_iter():
            min_ne, max_ne = map(int, node.annotations['ne'].value.strip("'").split("-"))
            if node.is_leaf():
                population_sizes[node.taxon.label.strip("'")] = [min_ne, max_ne]
            else:
                node_label = node.label.strip("'")
                population_sizes[node_label] = [min_ne, max_ne]
                min_div, max_div = map(int, node.annotations['div'].value.strip("'").split("-"))
                divergence_times[node_label] = [min_div, max_div]

    except (ValueError) as ve:
        raise ValueError(f"Error: Issue when getting priors from species tree: {ve}")
    except (KeyError) as ke:
        raise KeyError(f"Error: Issue when getting priors from species tree: {ke}")
    except Exception as e:
        raise Exception(f"Error: Issue when getting priors from species tree: {e}")

    return(population_sizes, divergence_times)

class NoAliasDumper(yaml.Dumper):
    def ignore_aliases(self, data):
        return True
