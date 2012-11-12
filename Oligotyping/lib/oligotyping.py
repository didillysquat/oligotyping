#!/usr/bin/python
# -*- coding: utf-8

# Copyright (C) 2010 - 2012, A. Murat Eren
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version.
#
# Please read the COPYING file.

__version__ = '0.6'

import os
import sys
import copy
import shutil
import cPickle

from Oligotyping.lib import fastalib as u
from Oligotyping.visualization.frequency_curve_and_entropy import vis_freq_curve
from Oligotyping.visualization.oligotype_sets_distribution import vis_oligotype_sets_distribution
from Oligotyping.visualization.oligotype_network_structure import oligotype_network_structure
from Oligotyping.visualization.oligotype_distribution_stack_bar import oligotype_distribution_stack_bar
from Oligotyping.visualization.oligotype_distribution_across_datasets import oligotype_distribution_across_datasets
from Oligotyping.utils.random_colors import random_colors
from Oligotyping.utils.random_colors import get_color_shade_dict_for_list_of_values
from Oligotyping.utils.cosine_similarity import get_oligotype_sets
from Oligotyping.utils.utils import P
from Oligotyping.utils.utils import Run
from Oligotyping.utils.utils import Progress
from Oligotyping.utils.utils import get_date
from Oligotyping.utils.utils import ConfigError
from Oligotyping.utils.utils import pretty_print
from Oligotyping.utils.utils import generate_MATRIX_files
from Oligotyping.utils.utils import generate_ENVIRONMENT_file 
from Oligotyping.utils.utils import get_unit_counts_and_percents
from Oligotyping.utils.utils import get_units_across_datasets_dicts
from Oligotyping.utils.utils import process_command_line_args_for_quality_files
from Oligotyping.utils.utils import generate_MATRIX_files_for_units_across_datasets

# FIXME: test whether Biopython is installed or not here.
from Oligotyping.utils.blast_interface import remote_blast_search, local_blast_search


class Oligotyping:
    def __init__(self, args = None):
        self.analysis = 'oligotyping'
        self.entropy   = None
        self.alignment = None
        self.quals_dict = None
        self.min_base_quality = None
        self.number_of_auto_components = 5
        self.selected_components = None
        self.limit_oligotypes_to = None
        self.exclude_oligotypes = None
        self.min_number_of_datasets = 5
        self.min_percent_abundance = 0.0
        self.min_actual_abundance = 0
        self.min_substantive_abundance = 0
        self.project = None
        self.output_directory = None
        self.dataset_name_separator = '_'
        self.limit_representative_sequences = sys.maxint
        self.quick = False
        self.no_figures = False
        self.no_display = False
        self.blast_ref_db = None
        self.skip_blast_search = False
        self.gen_html = False
        self.gen_dataset_oligo_networks = False
        self.colors_list_file = None
        self.generate_sets = False
        self.cosine_similarity_threshold = 0.1

        Absolute = lambda x: os.path.join(os.getcwd(), x) if not x.startswith('/') else x 

        if args:
            self.entropy = Absolute(args.entropy)
            self.alignment = Absolute(args.alignment)
            self.quals_dict = process_command_line_args_for_quality_files(args, _return = 'quals_dict')
            self.min_base_quality = args.min_base_quality
            self.number_of_auto_components = args.number_of_auto_components
            self.selected_components = args.selected_components
            self.limit_oligotypes_to = args.limit_oligotypes_to
            self.exclude_oligotypes = args.exclude_oligotypes
            self.min_number_of_datasets = args.min_number_of_datasets
            self.min_percent_abundance = args.min_percent_abundance
            self.min_actual_abundance = args.min_actual_abundance
            self.min_substantive_abundance = args.min_substantive_abundance
            self.project = args.project or os.path.basename(args.alignment).split('.')[0]
            self.output_directory = args.output_directory
            self.dataset_name_separator = args.dataset_name_separator
            self.limit_representative_sequences = args.limit_representative_sequences or sys.maxint
            self.quick = args.quick
            self.no_figures = args.no_figures
            self.no_display = args.no_display
            self.blast_ref_db = Absolute(args.blast_ref_db) if args.blast_ref_db else None
            self.skip_blast_search = args.skip_blast_search
            self.gen_html = args.gen_html
            self.gen_dataset_oligo_networks = args.gen_dataset_oligo_networks
            self.colors_list_file = args.colors_list_file
            self.cosine_similarity_threshold = args.cosine_similarity_threshold
            self.generate_sets = args.generate_sets
        
        self.run = Run()
        self.progress = Progress()

        self.datasets_dict = {}
        self.representative_sequences_per_oligotype = {}
        self.across_datasets_sum_normalized = {}
        self.across_datasets_max_normalized = {}
        self.unit_counts = None
        self.unit_percents = None
        self.oligotype_sets = None
        self.datasets = []
        self.abundant_oligos = []
        self.final_oligo_counts_dict = {}
        self.colors_dict = None

    def sanity_check(self):
        if (not os.path.exists(self.alignment)) or (not os.access(self.alignment, os.R_OK)):
            raise ConfigError, "Alignment file is not accessible: '%s'" % self.alignment
        
        if (not os.path.exists(self.entropy)) or (not os.access(self.entropy, os.R_OK)):
            raise ConfigError, "Entropy file is not accessible: '%s'" % self.entropy

        if self.number_of_auto_components != None and self.selected_components != None:
            raise ConfigError, "Both 'auto components' (-c) and 'selected components' (-C) has been declared."
        
        if self.number_of_auto_components == None and self.selected_components == None:
            raise ConfigError, "Either only 'auto components' (-c), or only 'selected components' (-C) can be declared."

        if self.selected_components:
            try:
                self.selected_components = [int(c) for c in self.selected_components.split(',')]
            except:
                raise ConfigError, "Selected components should be comma separated integer values (such as '4,8,15,25,47')."

        if self.min_base_quality:
            try:
                self.min_base_quality = int(self.min_base_quality)
                assert(self.min_base_quality >= 0 and self.min_base_quality <= 40)
            except:
                raise ConfigError, "Minimum base quality must be an integer between 0 and 40."

        if self.limit_oligotypes_to:
            self.limit_oligotypes_to = [o.strip().upper() for o in self.limit_oligotypes_to.split(',')]
            if len(self.limit_oligotypes_to) == 1:
                raise ConfigError, "There must be more than one oligotype for --limit-oligotypes parameter."

            if len([n for n in ''.join(self.limit_oligotypes_to) if n not in ['A', 'T', 'C', 'G', '-']]):
                raise ConfigError, "Oligotypes defined by --limit-oligotypes parameter seems to have ambiguous characters."

        if self.exclude_oligotypes:
            self.exclude_oligotypes = [o.strip().upper() for o in self.exclude_oligotypes.split(',')]
            
            if len([n for n in ''.join(self.exclude_oligotypes) if n not in ['A', 'T', 'C', 'G', '-']]):
                raise ConfigError, "Oligotypes defined by --exclude-oligotypes parameter seems to have ambiguous characters."
            
        
        if not self.output_directory:
            self.output_directory = os.path.join(os.getcwd(), '-'.join([self.project.replace(' ', '_'), self.get_prefix()]))
        
        if not os.path.exists(self.output_directory):
            try:
                os.makedirs(self.output_directory)
            except:
                raise ConfigError, "Output directory does not exist (attempt to create one failed as well): '%s'" % \
                                                                (self.output_directory)
        if not os.access(self.output_directory, os.W_OK):
            raise ConfigError, "You do not have write permission for the output directory: '%s'" % self.output_directory

        if self.colors_list_file:
            if not os.path.exists(self.colors_list_file):
                raise ConfigError, "Colors list file does not exist: '%s'" % self.colors_list_file
            first_characters = list(set([c.strip()[0] for c in open(self.colors_list_file)]))
            if len(first_characters) != 1 or first_characters[0] != '#':
                raise ConfigError, "Colors list file does not seem to be correctly formatted"


        return True


    def dataset_name_from_defline(self, defline):
        return self.dataset_name_separator.join(defline.split('|')[0].split(self.dataset_name_separator)[0:-1])


    def get_prefix(self):
        prefix = 's%d-a%.1f-A%d-M%d' % (self.min_number_of_datasets,
                                        self.min_percent_abundance,
                                        self.min_actual_abundance,
                                        self.min_substantive_abundance)

        if self.selected_components:
            prefix = 'sc%d-%s' % (len(self.selected_components), prefix)
        else:
            prefix = 'c%d-%s' % (self.number_of_auto_components, prefix)
        
        if self.quals_dict:
            prefix = '%s-q%d' % (prefix, self.min_base_quality)

        return prefix


    def generate_output_destination(self, postfix, directory = False):
        return_path = os.path.join(self.output_directory, postfix)

        if directory == True:
            if os.path.exists(return_path):
                shutil.rmtree(return_path)
            os.makedirs(return_path)

        return return_path


    def run_all(self):
        self.sanity_check()
        
        self.info_file_path = self.generate_output_destination('RUNINFO')
        self.run.init_info_file_obj(self.info_file_path)

        self.fasta = u.SequenceSource(self.alignment, lazy_init = False)
        self.column_entropy = [int(x.strip().split()[0]) for x in open(self.entropy).readlines()]

        self.fasta.next()
        self.alignment_length = len(self.fasta.seq)
        self.fasta.reset()

        self.run.info('project', self.project)
        self.run.info('run_date', get_date())
        self.run.info('version', __version__)
        self.run.info('alignment', self.alignment)
        self.run.info('entropy', self.entropy)
        self.run.info('output_directory', self.output_directory)
        self.run.info('info_file_path', self.info_file_path)
        self.run.info('quals_provided', True if self.quals_dict else False)
        self.run.info('cmd_line', ' '.join(sys.argv).replace(', ', ','))
        self.run.info('total_seq', pretty_print(self.fasta.total_seq))
        self.run.info('alignment_length', pretty_print(self.alignment_length))
        self.run.info('number_of_auto_components', self.number_of_auto_components or 0)
        self.run.info('number_of_selected_components', len(self.selected_components) if self.selected_components else 0)
        self.run.info('generate_sets', self.generate_sets)
        if self.generate_sets:
            self.run.info('T', self.cosine_similarity_threshold)
        self.run.info('s', self.min_number_of_datasets)
        self.run.info('a', self.min_percent_abundance)
        self.run.info('A', self.min_actual_abundance)
        self.run.info('M', self.min_substantive_abundance)
        if self.quals_dict:
            self.run.info('q', self.min_base_quality)
        if self.limit_oligotypes_to:
            self.run.info('limit_oligotypes_to', self.limit_oligotypes_to)
        if self.exclude_oligotypes:
            self.run.info('exclude_oligotypes', self.exclude_oligotypes)
        
        if self.number_of_auto_components:
            # locations of interest based on the entropy scores
            self.bases_of_interest_locs = sorted([self.column_entropy[i] for i in range(0, self.number_of_auto_components)])
            self.run.info('bases_of_interest_locs',', '.join([str(x) for x in self.bases_of_interest_locs]))
        elif self.selected_components:
            self.bases_of_interest_locs = sorted(self.selected_components)
            self.run.info('bases_of_interest_locs',', '.join([str(x) for x in self.bases_of_interest_locs]))

        if self.blast_ref_db:
            self.run.info('blast_ref_db', self.blast_ref_db)
        

        self._construct_datasets_dict()
        self._contrive_abundant_oligos()
        self._refine_datasets_dict()
        self._get_unit_counts_and_percents()
        self._generate_random_colors()
        
        self._generate_FASTA_file()
        self._generate_NEXUS_file()        
        self._generate_ENVIRONMENT_file()
        self._generate_MATRIX_files()
        
        if self.generate_sets:
            self._get_units_across_datasets_dicts()
            self._generate_MATRIX_files_for_units_across_datasets()
            self._agglomerate_oligos_based_on_cosine_similarity()
            self._generate_MATRIX_files_for_oligotype_sets()       
             
        if ((not self.no_figures) and (not self.quick)) and self.gen_dataset_oligo_networks:
            self._generate_dataset_oligotype_network_figures()
        if not self.no_figures:
            self._generate_stack_bar_figure()
            if self.generate_sets:
                self._generate_stack_bar_figure_with_agglomerated_oligos()
                self._generate_oligos_across_datasets_figure()
                self._generate_sets_across_datasets_figure()

        if not self.quick:
            self._generate_representative_sequences()

        if self.representative_sequences_per_oligotype:
            self._generate_representative_sequences_FASTA_file()

        info_dict_file_path = self.generate_output_destination("RUNINFO.cPickle")
        self.run.store_info_dict(info_dict_file_path)
        self.run.quit()

        if self.gen_html:
            self._generate_html_output()


    def _construct_datasets_dict(self):
        """This is where oligotypes are being genearted based on bases of each
           alignment at the location of interest"""

        self.progress.new('Dataset Dict Construction')

        if self.quals_dict:
            num_reads_eliminated_due_to_min_base_quality = 0

        self.fasta.reset()
        while self.fasta.next():
            if self.fasta.pos % 1000 == 0:
                self.progress.update('Analyzing: %s' \
                                    % (pretty_print(self.fasta.pos)))

            dataset = self.dataset_name_from_defline(self.fasta.id)
            
            if not self.datasets_dict.has_key(dataset):
                self.datasets_dict[dataset] = {}
                self.datasets.append(dataset)

            if self.quals_dict:
                # if qual_dicts is available, each base of interest will be tested
                # against --min-base-quality parameter to make sure that it is above
                # the expected quality score. 
                quality_scores = self.quals_dict[self.fasta.id]
                quality_scores_of_bases_of_interest = [quality_scores[o] for o in self.bases_of_interest_locs if not quality_scores[o] == None]
               
                min_base_quality = min([base_quality for base_quality in quality_scores_of_bases_of_interest if base_quality] or [0])

                if min_base_quality < self.min_base_quality:
                    # if True, discard the read
                    # FIXME: Discarded reads should be stored somewhere else for further analysis
                    num_reads_eliminated_due_to_min_base_quality += 1
                    continue
                else:
                    oligo = ''.join(self.fasta.seq[o] for o in self.bases_of_interest_locs)
                
            else:
                # if quals_dict is not available, oligotypes will be generated without
                # checking the base qualities
                oligo = ''.join(self.fasta.seq[o] for o in self.bases_of_interest_locs)
        
            if self.datasets_dict[dataset].has_key(oligo):
                self.datasets_dict[dataset][oligo] += 1
            else:
                self.datasets_dict[dataset][oligo] = 1
       
        self.datasets.sort()
        self.progress.reset()
        self.run.info('num_datasets_in_fasta', pretty_print(len(self.datasets_dict)))

        if self.quals_dict:
            self.run.info('num_reads_eliminated_due_to_min_base_quality', pretty_print(num_reads_eliminated_due_to_min_base_quality))
            if self.fasta.total_seq == num_reads_eliminated_due_to_min_base_quality:
                raise ConfigError, "All reads were eliminated due to --min-base-quality (%d) rule" % self.min_base_quality
        

    def _contrive_abundant_oligos(self):
        # cat oligos | uniq
        self.progress.new('Contriving Abundant Oligos')

        # a performance optimization workaround in order to lessen the 
        # number of expensive 'keys()' calls on datasets_dict to be made
        oligos_in_datasets_dict = {}
        for dataset in self.datasets:
            oligos_in_datasets_dict[dataset] = set(self.datasets_dict[dataset].keys())
        
        oligos_set = []
        for dataset in self.datasets:
            self.progress.update('Unique Oligos: ' + P(self.datasets.index(dataset), len(self.datasets)))
            for oligo in oligos_in_datasets_dict[dataset]:
                if oligo not in oligos_set:
                    oligos_set.append(oligo)
        self.progress.reset()
        self.run.info('num_unique_oligos', pretty_print(len(oligos_set)))
       

        # count oligo abundance
        oligo_dataset_abundance = []
        for i in range(0, len(oligos_set)):
            oligo = oligos_set[i]
            
            if i % 100 == 0 or i == len(oligos_set) - 1:
                self.progress.update('Counting oligo abundance: ' + P(i, len(oligos_set)))
            
            count = 0
            for dataset in self.datasets:
                if oligo in oligos_in_datasets_dict[dataset]:
                    count += 1
            oligo_dataset_abundance.append((count, oligo),)
        oligo_dataset_abundance.sort()
        self.progress.reset()

        # eliminate oligos based on the number of datasets they appear
        # (any oligo required to appear in at least 'self.min_number_of_datasets'
        # datasets)
        non_singleton_oligos = []
        for i in range(0, len(oligo_dataset_abundance)):
            if i % 100 == 0 or i == len(oligo_dataset_abundance) - 1:
                self.progress.update('Eliminating singletons: ' + P(i, len(oligo_dataset_abundance)))
            tpl = oligo_dataset_abundance[i]
            if tpl[0] >= self.min_number_of_datasets:
                non_singleton_oligos.append(tpl[1])
        self.progress.reset()
        self.run.info('num_oligos_after_s_elim', pretty_print(len(non_singleton_oligos)))
        

        # dataset_sums keeps the actual number of oligos that are present in non_singleton_oligos list,
        # for each dataset. computing it here once is more optimized.
        dataset_sums = {}
        SUM = lambda dataset: sum([self.datasets_dict[dataset][o] for o in non_singleton_oligos \
                                                                if self.datasets_dict[dataset].has_key(o)])
        for dataset in self.datasets:
            dataset_sums[dataset] = SUM(dataset)

        # eliminate very rare oligos (the percent abundance of every oligo should be
        # more than 'self.min_percent_abundance' percent in at least one dataset)
        for i in range(0, len(non_singleton_oligos)):
            oligo = non_singleton_oligos[i]
            if i % 100 == 0 or i == len(non_singleton_oligos) - 1:
                self.progress.update('Applying -a parameter: ' + P(i, len(non_singleton_oligos)))
            
            percent_abundances = []
            for dataset in self.datasets:
                if self.datasets_dict[dataset].has_key(oligo):
                    percent_abundances.append((self.datasets_dict[dataset][oligo] * 100.0 / dataset_sums[dataset],
                                               self.datasets_dict[dataset][oligo],
                                               dataset_sums[dataset],
                                               dataset))

            percent_abundances.sort(reverse = True)

            # NOTE: if a dataset has less than 100 sequences, percent abundance doesn't mean much.
            #       if user wants to eliminate oligotypes that doesn't appear in at least one dataset
            #       more than 1% abundance, a singleton of that oligotype that appears in a dataset
            #       which has 50 sequences would make that oligotype pass the filter. I think if an
            #       oligotype passes the percent filter, dataset size and actual count of the oligotype
            #       should also be considered before considering it as an abundant oligotype:
            for abundance_percent, abundance_count, dataset_size, dataset in percent_abundances:
                PercentAbundance_OK = abundance_percent >= self.min_percent_abundance
                DatesetSize_OK      = dataset_size > 100 or abundance_count > self.min_percent_abundance

                if PercentAbundance_OK and DatesetSize_OK:
                    self.abundant_oligos.append((sum([x[1] for x in percent_abundances]), oligo))
                    break

        self.progress.reset()
        self.run.info('num_oligos_after_a_elim', pretty_print(len(self.abundant_oligos)))
        
        self.abundant_oligos = [x[1] for x in sorted(self.abundant_oligos, reverse = True)]


        # eliminate very rare oligos (the ACTUAL ABUNDANCE, which is the sum of oligotype in all datasets
        # should should be more than 'self.min_actual_abundance'.
        if self.min_actual_abundance > 0:
            oligos_for_removal = []
            for i in range(0, len(self.abundant_oligos)):
                oligo = self.abundant_oligos[i]

                if i % 100 == 0 or i == len(self.abundant_oligos) - 1:
                    self.progress.update('Applying -A parameter: ' + P(i, len(non_singleton_oligos)))

                oligo_actual_abundance = sum([self.datasets_dict[dataset][oligo] for dataset in self.datasets_dict\
                                                        if self.datasets_dict[dataset].has_key(oligo)])
                if self.min_actual_abundance > oligo_actual_abundance:
                    oligos_for_removal.append(oligo)

            for oligo in oligos_for_removal:
                self.abundant_oligos.remove(oligo)
            self.progress.reset()
            self.run.info('num_oligos_after_A_elim', pretty_print(len(self.abundant_oligos)))


        # eliminate oligos based on -M / --min-substantive-abundance parameter.
        #
        # Here is a pesky problem. -A parameter eliminates oligotypes based on the number of sequences
        # represented by them. But this is not a very reliable way to eliminate noise, and sometimes it
        # eliminates more signal than noise. Here is an example: Say Oligotype #1 and Oligotype #2 both
        # represent 20 reads. But O#1 has only one unique sequence, so all reads that are being
        # represented by O#1 are actually the same. Meanwhile O#2 has 20 unique reads in it. So each
        # read differs from each other at bases that are not being used by oligotyping. Simply one could
        # argue that O#2 is full of noise, while O#1 is a robust oligotype that probably represents one
        # and only one organism. If you set -A to 25, both will be eliminated. But if there would be a
        # parameter that eliminates oligotypes based on the number of most abundant unique sequence
        # they entail, it could be set to, say '5', and O#1 would have survived that filter while O#2
        # the crappy oligotype would be filtered out. 
        #
        # Following function, _get_unique_sequence_distributions_within_abundant_oligos, returns the
        # dictionary that can be used to do that.
        #
        # And here is the ugly part about implementing this: This has to be done before the generation
        # of representative sequences. Upto the section where we generate representative sequences,
        # we only work with 'abundances' and we don't actually know what is the distribution of unique
        # sequences an oligotype conceals. This information is being computed when the representative
        # sequences are being computed. But in order to compute representative sequences we need to
        # know 'abundant' oligotypes first, and in order to finalize 'abundant' oligotypes
        # we need to run this cool filter. Chicken/egg. It is extremely inefficient, and I hate
        # to do this but this somewhat redundant step is mandatory and I can't think of any better
        # solution... And if you read this comment all the way here you either must be very bored or
        # very interested in using this codebase properly. Thanks.

        if self.min_substantive_abundance:
            oligos_for_removal = []
            unique_sequence_distributions = self._get_unique_sequence_distributions_within_abundant_oligos()

            for oligo in self.abundant_oligos:
                if max(unique_sequence_distributions[oligo]) < self.min_substantive_abundance:
                    oligos_for_removal.append(oligo)

            for oligo in oligos_for_removal:
                self.abundant_oligos.remove(oligo)

            self.progress.reset()
            self.run.info('num_oligos_after_M_elim', pretty_print(len(self.abundant_oligos)))


        # if 'limit_oligotypes_to' is defined, eliminate all other oligotypes
        if self.limit_oligotypes_to:
            self.abundant_oligos = [oligo for oligo in self.abundant_oligos if oligo in self.limit_oligotypes_to]
            self.run.info('num_oligos_after_l_elim', pretty_print(len(self.abundant_oligos)))
            if len(self.abundant_oligos) == 0:
                raise ConfigError, "Something is wrong; all oligotypes were eliminated with --limit-oligotypes. Quiting."

        # if 'exclude_oligotypes' is defined, remove them from analysis if they are present
        if self.exclude_oligotypes:
            self.abundant_oligos = [oligo for oligo in self.abundant_oligos if not oligo in self.exclude_oligotypes]
            self.run.info('num_oligos_after_e_elim', pretty_print(len(self.abundant_oligos)))


        # storing final counts
        for oligo in self.abundant_oligos:
            self.final_oligo_counts_dict[oligo] = sum([self.datasets_dict[dataset][oligo] for dataset in self.datasets_dict\
                                                        if self.datasets_dict[dataset].has_key(oligo)])

        self.progress.end()


    def _refine_datasets_dict(self):
        # removing oligos from datasets dictionary that didn't pass
        # MIN_PERCENT_ABUNDANCE_OF_OLIGOTYPE_IN_AT_LEAST_ONE_SAMPLE and
        # MIN_NUMBER_OF_SAMPLES_OLIGOTYPE_APPEARS filters.
        self.progress.new('Refining Datasets Dict')

        self.progress.update('Deepcopying datasets dict .. ')
        datasets_dict_copy = copy.deepcopy(self.datasets_dict)
        self.progress.append('done')

        datasets_to_remove = []
        for i in range(0, len(self.datasets)):
            dataset = self.datasets[i]

            self.progress.update('Analyzing datasets: ' + P(i + 1, len(self.datasets)))
            
            for oligo in datasets_dict_copy[dataset]:
                if oligo not in self.abundant_oligos:
                    self.datasets_dict[dataset].pop(oligo)
            if not self.datasets_dict[dataset]:
                datasets_to_remove.append(dataset)
        for dataset in datasets_to_remove:
            self.datasets.remove(dataset)
            self.datasets_dict.pop(dataset)

        self.progress.end()
        
        number_of_reads_in_datasets_dict = sum([sum(self.datasets_dict[dataset].values()) for dataset in self.datasets_dict]) 

        self.run.info('num_sequences_after_qc', '%s of %s (%.2f%%)'\
                            % (pretty_print(number_of_reads_in_datasets_dict),
                               pretty_print(self.fasta.total_seq),
                               number_of_reads_in_datasets_dict * 100.0 / self.fasta.total_seq))

        if len(datasets_to_remove):
            self.run.info('datasets_removed_after_qc', datasets_to_remove)               
        

    def _generate_FASTA_file(self): 
        # store abundant oligos
        self.progress.new('FASTA File')
        oligos_fasta_file_path = self.generate_output_destination("OLIGOS.fasta")
        f = open(oligos_fasta_file_path, 'w')
        self.progress.update('Being generated')
        for oligo in self.abundant_oligos:
            f.write('>' + oligo + '\n')
            f.write(oligo + '\n')
        f.close()
        self.progress.end()
        self.run.info('oligos_fasta_file_path', oligos_fasta_file_path)
 

    def _generate_representative_sequences_FASTA_file(self): 
        # store representative sequences per oligotype if they are computed
        self.progress.new('Representative Sequences FASTA File')
        representative_seqs_fasta_file_path = self.generate_output_destination("OLIGO-REPRESENTATIVES.fasta")
        f = open(representative_seqs_fasta_file_path, 'w')
        self.progress.update('Being generated')
        for oligo in self.abundant_oligos:
            f.write('>' + oligo + '\n')
            f.write(self.representative_sequences_per_oligotype[oligo] + '\n')
        f.close()
        self.progress.end()
        self.run.info('representative_seqs_fasta_file_path', representative_seqs_fasta_file_path)
        
        
    def _generate_NEXUS_file(self):
        # generate NEXUS file of oligos
        self.progress.new('NEXUS File')
        oligos_nexus_file_path = self.generate_output_destination("OLIGOS.nexus")
        f = open(oligos_nexus_file_path, 'w')
        f.write("""begin data;
            dimensions ntax=%d nchar=%d;
            format datatype=dna interleave=no gap=-;
            matrix\n""" % (len(self.abundant_oligos), len(self.abundant_oligos[0])))
        self.progress.update('Being generated')
        for oligo in self.abundant_oligos:
            f.write('    %.40s %s\n' % (oligo, oligo))
        f.write('    ;\n')
        f.write('end;\n')
        f.close()
        self.progress.end()
        self.run.info('oligos_nexus_file_path', oligos_nexus_file_path)


    def _get_unit_counts_and_percents(self):
        self.progress.new('Unit counts and percents')
        self.progress.update('Data is being generated')
            
        self.unit_counts, self.unit_percents = get_unit_counts_and_percents(self.abundant_oligos, self.datasets_dict)
            
        self.progress.end()


    def _generate_MATRIX_files_for_units_across_datasets(self):
        self.progress.new('Oligos across datasets')
        self.progress.update('Matrix files are being generated')

        across_datasets_MN_file_path = self.generate_output_destination("OLIGOS-ACROSS-DATASETS-MAX-NORM.txt")
        across_datasets_SN_file_path = self.generate_output_destination("OLIGOS-ACROSS-DATASETS-SUM-NORM.txt")
             
        generate_MATRIX_files_for_units_across_datasets(self.abundant_oligos,
                                                        self.datasets,
                                                        across_datasets_MN_file_path,
                                                        across_datasets_SN_file_path,
                                                        self.across_datasets_max_normalized,
                                                        self.across_datasets_sum_normalized)

        self.progress.end()
        self.run.info('across_datasets_MN_file_path', across_datasets_MN_file_path)
        self.run.info('across_datasets_SN_file_path', across_datasets_SN_file_path)


    def _get_units_across_datasets_dicts(self):
        self.progress.new('Oligos across datasets')
        self.progress.update('Data is being generated')

        self.across_datasets_sum_normalized, self.across_datasets_max_normalized =\
                get_units_across_datasets_dicts(self.abundant_oligos, self.datasets, self.unit_percents) 
            
        self.progress.end()

 
    def _generate_ENVIRONMENT_file(self):
        self.progress.new('ENVIRONMENT File')
        environment_file_path = self.generate_output_destination("ENVIRONMENT.txt")
        self.progress.update('Being generated')
        
        generate_ENVIRONMENT_file(self.datasets,
                                  self.datasets_dict,
                                  environment_file_path)

        self.progress.end()
        self.run.info('environment_file_path', environment_file_path)
        
    def _generate_MATRIX_files(self):
        self.progress.new('Matrix Files')
        self.progress.update('Being generated')
            
        matrix_count_file_path = self.generate_output_destination("MATRIX-COUNT.txt")
        matrix_percent_file_path = self.generate_output_destination("MATRIX-PERCENT.txt")    
            
        generate_MATRIX_files(self.abundant_oligos,
                              self.datasets,
                              self.unit_counts,
                              self.unit_percents,
                              matrix_count_file_path,
                              matrix_percent_file_path)
            
        self.progress.end()
        self.run.info('matrix_count_file_path', matrix_count_file_path)
        self.run.info('matrix_percent_file_path', matrix_percent_file_path)

    def _generate_random_colors(self):
        colors_file_path = self.generate_output_destination('COLORS')
        if self.colors_list_file:
            # it means user provided a list of colors to be used for oligotypes
            colors = [c.strip() for c in open(self.colors_list_file).readlines()]
            if len(colors) < len(self.abundant_oligos):
                raise ConfigError, "Number of colors defined in colors file (%d),\
                                    is smaller than the number of abundant oligotypes (%d)" % \
                                                        (len(colors), len(self.abundant_oligos))
            colors_dict = {}
            for i in range(0, len(self.abundant_oligos)):
                colors_dict[self.abundant_oligos[i]] = colors[i]

            self.colors_dict = colors_dict
            
            # generate COLORS file derived from --colors-list-file
            colors_file = open(colors_file_path, 'w')
            for oligotype in self.abundant_oligos:
                colors_file.write('%s\t%s\n' % (oligotype, self.colors_dict[oligotype]))
            colors_file.close()

        else:
            self.colors_dict = random_colors(self.abundant_oligos, colors_file_path)
        self.run.info('colors_file_path', colors_file_path)


    def _agglomerate_oligos_based_on_cosine_similarity(self):
        self.progress.new('Agglomerating Oligotypes into Sets')
        oligotype_sets_file_path = self.generate_output_destination("OLIGOTYPE-SETS.txt")
        self.progress.update('Computing')
        self.oligotype_sets = get_oligotype_sets(self.abundant_oligos,
                                                 self.across_datasets_sum_normalized,
                                                 self.cosine_similarity_threshold,
                                                 oligotype_sets_file_path)
        
        self.progress.end()
        self.run.info('oligotype_sets_file_path', oligotype_sets_file_path)
        self.run.info('oligotype_sets_info', '%d oligotypes agglomerated into %d sets'\
                                            % (len(self.abundant_oligos), len(self.oligotype_sets)))


        self.progress.new('Generating data objects for newly generated oligotype sets')
        self.progress.update('New Colors')
        self.oligotype_set_ids = range(0, len(self.oligotype_sets))
        
        self.colors_dict_for_oligotype_sets = {}
        for set_id in self.oligotype_set_ids:
            self.colors_dict_for_oligotype_sets[set_id] = self.colors_dict[self.oligotype_sets[set_id][0]]

        self.progress.update('New Datasets Dict')
        self.datasets_dict_with_agglomerated_oligos = {}
        for dataset in self.datasets:
            self.datasets_dict_with_agglomerated_oligos[dataset] = {}

        for set_id in self.oligotype_set_ids:
            oligotype_set = self.oligotype_sets[set_id]
            for dataset in self.datasets:
                self.datasets_dict_with_agglomerated_oligos[dataset][set_id] = 0
                for oligo in self.datasets_dict[dataset]:
                    if oligo in oligotype_set:
                        self.datasets_dict_with_agglomerated_oligos[dataset][set_id] += self.datasets_dict[dataset][oligo]

        self.progress.end()


    def _generate_MATRIX_files_for_oligotype_sets(self):
        self.progress.new('Matrix Files for Oligotype Sets')
        counts_file_path = self.generate_output_destination("MATRIX-COUNT-OLIGO-SETS.txt")
        percents_file_path = self.generate_output_destination("MATRIX-PERCENT-OLIGO-SETS.txt")
        
        d = self.datasets_dict_with_agglomerated_oligos
        oligotype_set_percents = {}
        oligotype_set_counts = {}


        self.progress.update('Generating the data')
        for oligotype_set_id in self.oligotype_set_ids:
            counts = []
            percents = []
            for dataset in self.datasets:
                if d[dataset].has_key(oligotype_set_id):
                    counts.append(d[dataset][oligotype_set_id])
                    percents.append(d[dataset][oligotype_set_id] * 100.0 / sum(d[dataset].values()))
                else:
                    counts.append(0)
                    percents.append(0.0)

            oligotype_set_percents[oligotype_set_id] = percents
            oligotype_set_counts[oligotype_set_id] = counts
        
        self.progress.update('Generating files')
        counts_file = open(counts_file_path, 'w')
        percents_file = open(percents_file_path, 'w')       
        
        counts_file.write('\t'.join([''] + self.datasets) + '\n')
        percents_file.write('\t'.join([''] + self.datasets) + '\n')

        for oligotype_set_id in self.oligotype_set_ids:
            counts_file.write('\t'.join(['Set_' + str(oligotype_set_id)] + [str(c) for c in oligotype_set_counts[oligotype_set_id]]) + '\n')
            percents_file.write('\t'.join(['Set_' + str(oligotype_set_id)] + [str(p) for p in oligotype_set_percents[oligotype_set_id]]) + '\n')
        
        counts_file.close()
        percents_file.close()

        self.progress.end()
        self.run.info('matrix_count_oligo_sets_file_path', counts_file_path)
        self.run.info('matrix_percent_oligo_sets_file_path', percents_file_path)


    def _get_unique_sequence_distributions_within_abundant_oligos(self):
        # compute and return the unique sequence distribution within per oligo
        # dictionary. see the explanation where the function is called.

        self.progress.new('Unique Sequence Distributions Within Abundant Oligos')

        self.unique_sequence_distribution_per_oligo = dict(zip(self.abundant_oligos, [{} for x in range(0, len(self.abundant_oligos))]))

        self.fasta.reset()
        while self.fasta.next():
            if self.fasta.pos % 1000 == 0:
                self.progress.update('Computing: %.2f%%' \
                                                % (self.fasta.pos * 100 / self.fasta.total_seq))
            oligo = ''.join(self.fasta.seq[o] for o in self.bases_of_interest_locs)
            if oligo in self.abundant_oligos:
                try:
                    self.unique_sequence_distribution_per_oligo[oligo][self.fasta.seq] += 1
                except KeyError:
                    self.unique_sequence_distribution_per_oligo[oligo][self.fasta.seq] = 1

        for oligo in self.abundant_oligos:
            self.unique_sequence_distribution_per_oligo[oligo] = sorted(self.unique_sequence_distribution_per_oligo[oligo].values(), reverse = True)

        self.progress.end()

        return self.unique_sequence_distribution_per_oligo


    def _generate_representative_sequences(self):
        # create a fasta file with a representative full length consensus sequence for every oligotype

        # this is what is going on here: we go through all oligotypes, gather sequences that are being
        # represented by a particular oligotype, unique them and report the top ten unique sequences
        # ordered by the frequency.
        self.progress.new('Represenative Sequences')

        output_directory_for_reps = self.generate_output_destination("OLIGO-REPRESENTATIVES", directory = True)


        fasta_files_dict = {}
        unique_files_dict = {}
        for oligo in self.abundant_oligos:
            if oligo not in fasta_files_dict:
                try:
                    fasta_file_path = os.path.join(output_directory_for_reps, '%.5d_' % self.abundant_oligos.index(oligo) + oligo)
                    fasta_files_dict[oligo] = {'file': open(fasta_file_path, 'w'),
                                               'path': fasta_file_path}
                    unique_files_dict[oligo] = {'file': open(fasta_file_path + '_unique', 'w'),
                                                'path': fasta_file_path + '_unique'}
                except IOError:
                    print '\n\t'.join(['',
                                       'WARNING: Oligotyping process has reached the maximum number of open files',
                                       'limit defined by the operating system. There are "%d" oligotypes to be'\
                                                                 % len(self.abundant_oligos),
                                       'stored. You can learn the actual limit by typing "ulimit -n" in the.run.',
                                       '',
                                       'You can increase this limit temporarily by typing "ulimit -n NUMBER", and',
                                       'restart the process. It seems using %d as NUMBER might be a good start.'\
                                                                % (len(self.abundant_oligos) * 1.1),
                                       '',
                                       'Until this issue is solved, representative sequences are not going to be',
                                       'computed.',
                                       ''])

                    # clean after yourself. close every file, delete directory, exit.
                    [map(lambda x: x.close(), [g[o]['file'] for o in g]) for g in [fasta_files_dict, unique_files_dict]]
                    shutil.rmtree(output_directory_for_reps)
                    sys.exit()

        self.fasta.reset()
        while self.fasta.next():
            if self.fasta.pos % 1000 == 0:
                self.progress.update('Generating Individual FASTA Files: %.2f%%' \
                                                % (self.fasta.pos * 100 / self.fasta.total_seq))
            oligo = ''.join(self.fasta.seq[o] for o in self.bases_of_interest_locs)
            if oligo in self.abundant_oligos:
                fasta_files_dict[oligo]['file'].write('>%s\n' % (self.fasta.id))
                fasta_files_dict[oligo]['file'].write('%s\n' % self.fasta.seq)
        
        self.progress.end()

        for oligo in self.abundant_oligos:
            self.progress.new('Representative Sequences | %s (%d of %d)'\
                % (oligo, self.abundant_oligos.index(oligo) + 1, len(self.abundant_oligos)))
            fasta_files_dict[oligo]['file'].close()

            fasta_file_path = fasta_files_dict[oligo]['path']
            fasta = u.SequenceSource(fasta_file_path, lazy_init = False, unique = True)
          
            # this dict is going to hold the information of how unique sequences within an oligotype
            # is distributed among datasets:
            distribution_among_datasets = {}

            self.progress.update('Unique reads in FASTA ..') 

            fasta.next()
            # this is the first read in the unique reads list, which is the most abundant unique sequence
            # for the oligotype. so we are going to store it in a dict to generate
            # representative sequences FASTA file:
            self.representative_sequences_per_oligotype[oligo] = fasta.seq
            fasta.reset()

            while fasta.next() and fasta.pos <= self.limit_representative_sequences:
                unique_files_dict[oligo]['file'].write('>%s_%d|freq:%d\n'\
                                                                     % (oligo,
                                                                        fasta.pos,
                                                                        len(fasta.ids)))
                unique_files_dict[oligo]['file'].write('%s\n' % fasta.seq)

                for dataset_id in fasta.ids:
                    dataset_name = self.dataset_name_from_defline(dataset_id)
                    if not distribution_among_datasets.has_key(dataset_name):
                        distribution_among_datasets[dataset_name] = {}
                    d = distribution_among_datasets[dataset_name]
                    if not d.has_key(fasta.pos):
                        d[fasta.pos] = 1
                    else:
                        d[fasta.pos] += 1
                
            fasta.close()
            unique_files_dict[oligo]['file'].close()

            unique_fasta_path = unique_files_dict[oligo]['path']
            distribution_among_datasets_dict_path = unique_fasta_path + '_distribution.cPickle'
            cPickle.dump(distribution_among_datasets, open(distribution_among_datasets_dict_path, 'w'))

            if (not self.quick) and (not self.skip_blast_search):
                # perform BLAST search and store results
                unique_fasta = u.SequenceSource(unique_fasta_path)
                unique_fasta.next()
               
                if self.blast_ref_db:
                    # if self.blast_ref_db is set, then perform a local BLAST search 
                    # against self.blast_ref_db
                    oligo_representative_blast_output = unique_fasta_path + '_BLAST.txt'

                    self.progress.update('Local BLAST Search..')
                        
                    local_blast_search(unique_fasta.seq, self.blast_ref_db, oligo_representative_blast_output)

                else:
                    # if self.blast_ref_db is not set, perform a BLAST search on NCBI
                    oligo_representative_blast_output = unique_fasta_path + '_BLAST.xml'

                    # FIXME: this value should be paramaterized
                    max_blast_attempt = 3

                    def blast_search_wrapper(seq, blast_output):
                        try:
                            remote_blast_search(seq, blast_output)
                            return True
                        except:
                            return False

                    for blast_attempt in range(0, max_blast_attempt):
                        self.progress.update('NCBI BLAST search (attempt #%d)' % (blast_attempt + 1))
                            
                        if blast_search_wrapper(unique_fasta.seq, oligo_representative_blast_output):
                            break
                        else:
                            continue

                unique_fasta.close()

            if (not self.quick) and (not self.no_figures):
                entropy_file_path = unique_fasta_path + '_entropy'
                color_per_column_path  = unique_fasta_path + '_color_per_column.cPickle'

                # generate entropy output at 'entropy_file_path' along with the image
                self.progress.update('Generating entropy figure')
                vis_freq_curve(unique_fasta_path, output_file = unique_fasta_path + '.png', entropy_output_file = entropy_file_path)

                # use entropy output to generate a color shade for every columns in alignment
                # for visualization purposes
                entropy_values_per_column = [0] * self.alignment_length
                for column, entropy in [x.strip().split('\t') for x in open(entropy_file_path)]:
                    entropy_values_per_column[int(column)] = float(entropy)
                color_shade_dict = get_color_shade_dict_for_list_of_values(entropy_values_per_column)

                color_per_column = [0] * self.alignment_length
                for i in range(0, self.alignment_length):
                    color_per_column[i] = color_shade_dict[entropy_values_per_column[i]]        

                cPickle.dump(color_per_column, open(color_per_column_path, 'w'))
       
        self.progress.end()
        self.run.info('output_directory_for_reps', output_directory_for_reps) 


    def _generate_dataset_oligotype_network_figures(self):
        output_directory_for_datasets = self.generate_output_destination("DATASETS", directory = True)
        oligotype_network_structure(self.run.info_dict['environment_file_path'], output_dir = output_directory_for_datasets)
        self.run.info('output_directory_for_datasets', output_directory_for_datasets) 
 

    def _generate_stack_bar_figure(self):
        self.progress.new('Stackbar Figure')
        stack_bar_file_path = self.generate_output_destination('STACKBAR.png')
        self.progress.update('Generating')
        oligos = copy.deepcopy(self.abundant_oligos)
        oligotype_distribution_stack_bar(self.datasets_dict, self.colors_dict, stack_bar_file_path, oligos = oligos,\
                                         project_title = self.project, display = ((not self.no_display) and self.quick))
        self.progress.end()
        self.run.info('stack_bar_file_path', stack_bar_file_path)


    def _generate_oligos_across_datasets_figure(self):
        self.progress.new('Oligotypes Across Datasets Figure')
        oligos_across_datasets_file_path = self.generate_output_destination('OLIGOS-ACROSS-DATASETS.png')
        self.progress.update('Generating')
        oligos = copy.deepcopy(self.abundant_oligos)
        oligotype_distribution_across_datasets(self.datasets_dict, self.colors_dict, oligos_across_datasets_file_path,\
                                               oligos = oligos, project_title = self.project, display = False)
        self.progress.end()
        self.run.info('oligos_across_datasets_file_path', oligos_across_datasets_file_path)


    def _generate_sets_across_datasets_figure(self):
        self.progress.new('Oligotype Sets Across Datasets Figure')
        figure_path = self.generate_output_destination('OLIGO-SETS-ACROSS-DATASETS.png')
        self.progress.update('Generating')
        vis_oligotype_sets_distribution(self.oligotype_sets, self.across_datasets_sum_normalized, self.datasets,\
                               display = False, colors_dict = self.colors_dict, output_file = figure_path,\
                               project_title = 'Oligotype Sets Across Datasets for "%s", at Cosine Similarity Threshold of %.4f'\
                                        % (self.project, self.cosine_similarity_threshold), legend = False)
        self.progress.end()
        self.run.info('oligotype_sets_across_datasets_figure_path', figure_path)


    def _generate_stack_bar_figure_with_agglomerated_oligos(self):
        self.progress.new('Stackbar Figure with Agglomerated Oligos')
        stack_bar_file_path = self.generate_output_destination('STACKBAR-AGGLOMERATED-OLIGOS.png')
        self.progress.update('Generating')

        oligotype_distribution_stack_bar(self.datasets_dict_with_agglomerated_oligos, self.colors_dict_for_oligotype_sets,\
                                         stack_bar_file_path, oligos = self.oligotype_set_ids, project_title = self.project,\
                                         display = not self.no_display)
        self.progress.end()
        self.run.info('stack_bar_with_agglomerated_oligos_file_path', stack_bar_file_path)


    def _generate_html_output(self):
        from Oligotyping.utils.html.error import HTMLError
        try:
            from Oligotyping.utils.html.generate import generate_html_output
        except HTMLError, e:
            sys.stdout.write('\n\n\t%s\n\n' % e)
            sys.exit()

        self.progress.new('HTML Output')
        output_directory_for_html = self.generate_output_destination("HTML-OUTPUT", directory = True)
        self.progress.update('Generating')
        index_page = generate_html_output(self.run.info_dict, html_output_directory = output_directory_for_html)
        self.progress.end()
        sys.stdout.write('\n\n\tView results in your browser: "%s"\n\n' % index_page)


if __name__ == '__main__':
    pass
