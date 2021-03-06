#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# (c) The James Hutton Institute 2017-2019
# (c) The University of Strathclude 2019
# Author: Leighton Pritchard
#
# Contact:
# leighton.pritchard@strath.ac.uk
#
# Leighton Pritchard,
# Strathclyde Institute of Pharmaceutical and Biomedical Sciences
# The University of Strathclyde
#  Cathedral Street
# Glasgow
#  G1 1XQ
# Scotland,
# UK
#
# The MIT License
#
# Copyright (c) 2017-2018 The James Hutton Institute
# (c) The University of Strathclude 2019
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
"""Provides the plot subcommand for pyani."""

import os

from argparse import Namespace
from logging import Logger
from pathlib import Path
from typing import Dict, List

import matplotlib.pyplot as plt
import pandas as pd

from pyani import pyani_config, pyani_orm, pyani_graphics
from pyani.pyani_tools import MatrixData


# Distribution dictionary of matrix graphics methods
GMETHODS = {"mpl": pyani_graphics.mpl.heatmap, "seaborn": pyani_graphics.sns.heatmap}
# Distribution dictionary of distribution graphics methods
DISTMETHODS = {
    "mpl": pyani_graphics.mpl.distribution,
    "seaborn": pyani_graphics.sns.distribution,
}


def subcmd_plot(args: Namespace, logger: Logger) -> int:
    """Produce graphical output for an analysis.

    :param args:  Namespace of command-line arguments
    :param logger:  logging object

    This is graphical output for representing the ANI analysis results, and
    takes the form of a heatmap, or heatmap with dendrogram.
    """
    # Announce what's going on to the user
    logger.info("Generating graphical output for analyses")
    logger.info(f"Writing output to: {args.outdir}")
    os.makedirs(args.outdir, exist_ok=True)
    logger.info(f"Rendering method: {args.method}")

    # Connect to database session
    logger.info(f"Activating session for database: {args.dbpath}")
    session = pyani_orm.get_session(args.dbpath)

    # Parse output formats
    outfmts = args.formats.split(",")
    logger.info(f"Requested output formats: {outfmts}")

    # Work on each run:
    run_ids = [int(run) for run in args.run_id.split(",")]
    logger.info(f"Generating graphics for runs: {run_ids}")
    for run_id in run_ids:
        write_run_heatmaps(run_id, session, outfmts, args, logger)

    return 0


def write_run_heatmaps(
    run_id: int, session, outfmts: List[str], args: Namespace, logger: Logger
) -> None:
    """Write all heatmaps for a specified run to file.

    :param run_id:  int, run identifier in database session
    :param session:  Session, active SQLite session
    :param outfmts:  list of output format types
    :param args:  Namespace, command line arguments
    :param logger:  logging object
    """
    # Get results matrices for the run
    logger.info(f"Acquiring results for run {run_id}")
    logger.info("\t...retrieving results matrices")
    results = (
        session.query(pyani_orm.Run).filter(pyani_orm.Run.run_id == args.run_id).first()
    )
    result_label_dict = pyani_orm.get_matrix_labels_for_run(session, args.run_id)
    result_class_dict = pyani_orm.get_matrix_classes_for_run(session, args.run_id)

    # Write heatmap for each results matrix
    for matdata in [
        MatrixData(*_)
        for _ in [
            ("identity", pd.read_json(results.df_identity), {}),
            ("coverage", pd.read_json(results.df_coverage), {}),
            ("aln_lengths", pd.read_json(results.df_alnlength), {}),
            ("sim_errors", pd.read_json(results.df_simerrors), {}),
            ("hadamard", pd.read_json(results.df_hadamard), {}),
        ]
    ]:
        write_heatmap(
            run_id, matdata, result_label_dict, result_class_dict, outfmts, args, logger
        )
        write_distribution(run_id, matdata, outfmts, args, logger)


def write_distribution(
    run_id: int,
    matdata: MatrixData,
    outfmts: List[str],
    args: Namespace,
    logger: Logger,
) -> None:
    """Write distribution plots for each matrix type.

    :param run_id:  int, run_id for this run
    :param matdata:  MatrixData object for this distribution plot
    :param args:  Namespace for command-line arguments
    :param outfmts:  list of output formats for files
    :param logger:  logging object
    """
    logger.info(f"Writing distribution plot for {matdata.name} matrix")
    for fmt in outfmts:
        outfname = Path(args.outdir) / f"distribution_{matdata.name}_run{run_id}.{fmt}"
        logger.info(f"\tWriting graphics to {outfname}")
        DISTMETHODS[args.method](
            matdata.data,
            outfname,
            matdata.name,
            title=f"matrix_{matdata.name}_run{run_id}",
        )


def write_heatmap(
    run_id: int,
    matdata: MatrixData,
    result_labels: Dict,
    result_classes: Dict,
    outfmts: List[str],
    args: Namespace,
    logger: Logger,
) -> None:
    """Write a single heatmap for a pyani run.

    :param run_id:  int, run_id for this run
    :param matdata:  MatrixData object for this heatmap
    :param result_labels:  dict of result labels
    :param result_classes: dict of result classes
    :param args:  Namespace for command-line arguments
    :param outfmts:  list of output formats for files
    :param logger:  logging object
    """
    logger.info(f"Writing {matdata.name} matrix heatmaps")
    cmap = pyani_config.get_colormap(matdata.data, matdata.name)
    for fmt in outfmts:
        outfname = Path(args.outdir) / f"matrix_{matdata.name}_run{run_id}.{fmt}"
        logger.info(f"\tWriting graphics to {outfname}")
        params = pyani_graphics.Params(cmap, result_labels, result_classes)
        # Draw heatmap
        GMETHODS[args.method](
            matdata.data,
            outfname,
            title=f"matrix_{matdata.name}_run{run_id}",
            params=params,
        )

    # Be tidy with matplotlib caches
    plt.close("all")
