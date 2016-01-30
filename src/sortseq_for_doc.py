#!/usr/bin/env python2.7

''' Primary function for sst.ools. Currently supports: 

simulate_library
simulate_sublib
simulate_sst
simulate_selection
simulate_mpra
'''

from __future__ import division
import numpy as np
import scipy as sp
import argparse
import sys
import csv

# Create argparse parser. 
parser = argparse.ArgumentParser()

# All functions can specify and output file. Default is stdout.
parser.add_argument('-o','--out',default=False,help='Output location/type, by default it writes to standard output, if a file name is supplied it will write to a text file')

# Add various subcommands individually viva subparsers
subparsers = parser.add_subparsers()

# preprocess
import sst.preprocess as preprocess
preprocess.add_subparser(subparsers)

#profile_mutrate
import sst.profile_mut as profile_mut
profile_mut.add_subparser(subparsers)

#learn_matrix
import sst.learn_matrix as learn_matrix
learn_matrix.add_subparser(subparsers)

#predictiveinfo
import sst.predictiveinfo as predictiveinfo
predictiveinfo.add_subparser(subparsers)

#profile_info
import sst.profile_info as profile_info
profile_info.add_subparser(subparsers)

#Scan
import sst.Scan as Scan
Scan.add_subparser(subparsers)

#simualte_library
import sst.simulate_library as simulate_library
simulate_library.add_subparser(subparsers)

#simulate_sort
import sst.simulate_sort as simulate_sort
simulate_sort.add_subparser(subparsers)

#simulate_evaluate
import sst.simulate_evaluate as simulate_evaluate
simulate_evaluate.add_subparser(subparsers)

#simulate_sort
import sst.simulate_expression as simulate_expression
simulate_expression.add_subparser(subparsers)

# Final incantiation needed for this to work








