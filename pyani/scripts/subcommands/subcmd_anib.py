#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# (c) The James Hutton Institute 2017-2019
#
# Author: Leighton Pritchard
# Contact: leighton.pritchard@hutton.ac.uk
#
# Leighton Pritchard,
# Information and Computing Sciences,
# James Hutton Institute,
# Errol Road,
# Invergowrie,
# Dundee,
# DD6 9LH,
# Scotland,
# UK
#
# The MIT License
#
# Copyright (c) 2017-2019 The James Hutton Institute
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
"""Provides the anib subcommand for pyani."""

import datetime
import json
import os

from argparse import Namespace
from itertools import permutations
from logging import Logger
from pathlib import Path
from typing import List, Tuple

from Bio import SeqIO
from tqdm import tqdm

from pyani import anib
from pyani.pyani_files import collect_existing_output
from pyani.pyani_orm import (
    PyaniORMException,
    add_run,
    add_run_genomes,
    filter_existing_comparisons,
    get_session,
    update_comparison_matrices,
)


def subcmd_anib(args: Namespace, logger: Logger) -> None:
    """Perform ANIb on all genome files in an input directory.

    :param args:  Namespace, command-line arguments
    :param logger:  logging object

    Finds ANI by the ANIb method, as described in Goris J, Konstantinidis KT,
    Klappenbach JA, Coenye T, Vandamme P, et al. (2007) DNA-DNA hybridization
    values and their relationship to whole-genome sequence similarities.
    Int J Syst Evol Micr 57: 81-91. doi:10.1099/ijs.0.64483-0.

    All FASTA format files (selected by suffix) in the input directory are
    fragmented into (by default 1020nt) consecutive sections, and a BLAST+
    database constructed from the whole genome input. The BLAST+ blastn tool
    is then used to query each set of fragments against each BLAST+ database,
    in turn.

    For each query, the BLAST+ .tab output is parsed to obtain alignment length,
    identity and similarity error count. Alignments below a threshold are not
    included in the calculation (this introduces systematic bias with respect to
    ANIm). The results are processed to calculate the ANI percentages, coverage,
    and similarity error.

    The calculated values are stored in the local SQLite3 database.
    """
    logger.info("Running ANIm analysis")  # announce that we're starting

    # Get BLAST+ version - this will be used in the database entries
    blastn_version = anib.get_version(args.blastn_exe)
    logger.info(f"BLAST+ blastn version: {blastn_version}")

    # Use provided name, or make new one for this analysis
    start_time = datetime.datetime.now()
    name = args.name or "_".join(["ANIb", start_time.isoformat()])
    logger.info(f"Analysis name: {name}")

    # Connect to existing database (which may be "clean" or have old analyses)
    logger.info(f"Connecting to database {args.dbpath}")
    try:
        session = get_session(args.dbpath)
    except Exception:
        logger.error(
            f"Could not connect to database {args.dbpath} (exiting)", exc_info=True
        )
        raise SystemExit(1)

    # Add information about this run to the database
    logger.info(f"Adding run info to database {args.dbpath}...")
    try:
        run = add_run(
            session,
            method="ANIb",
            cmdline=args.cmdline,
            date=start_time,
            status="started",
            name=name,
        )
    except PyaniORMException:
        logger.error("Could not add run to the database (exiting)", exc_info=True)
        raise SystemExit(1)
    logger.info(f"\t...added run ID: {run} to the database")

    # Identify input files for comparison, and populate the database
    logger.info(f"Adding files for {run} to database...")
    try:
        genome_ids = add_run_genomes(
            session, run, args.indir, args.classes, args.labels
        )
    except PyaniORMException:
        logger.error(
            f"Could not add genomes to database for run {run} (exiting)", exc_info=True
        )
    logger.info(f"\t...added genome IDs: {genome_ids}")

    # Get list of genomes for this analysis from the database
    logger.info("Compiling genomes for comparison")
    genomes = run.genomes.all()
    logger.info(f"\tCollected {len(genomes)} genomes for this run")

    # Create output directories. We create the main parent directory (args.outdir), but
    # also subdirectories for the BLAST databases,
    logger.info(f"Creating output directory {args.outdir}")
    try:
        os.makedirs(args.outdir, exist_ok=True)
    except IOError:
        logger.error(
            f"Could not create output directory {args.outdir} (exiting)", exc_info=True
        )
        raise SystemError(1)
    fragdir = Path(str(args.outdir)) / "fragments"
    blastdbdir = Path(str(args.outdir)) / "blastdbs"
    logger.info(f"\t...creating subdirectories")
    os.makedirs(fragdir, exist_ok=True)
    os.makedirs(blastdbdir, exist_ok=True)

    # Create a new sequence fragment file and a new BLAST+ database for each input genome,
    # and add this data to the database as a row in BlastDB
    logger.info("Creating input sequence fragment files...")
    for genome in genomes:
        fragpath, fraglengths = fragment_fasta_file(
            Path(str(genome.path)), Path(str(fragdir)), args.fragsize
        )
        print(fragpath, len(fraglengths))
        # blastdb = add_blastdb(
        #     session, genome, run, fragpath, dbpath, fraglengths, dbcmd
        # )

    raise NotImplementedError

    # Generate all pair permutations of genome IDs as a list of (Genome, Genome) tuples
    logger.info(
        "Compiling pairwise comparisons (this can take time for large datasets)..."
    )
    comparisons = list(permutations(tqdm(genomes, disable=args.disable_tqdm), 2))
    logger.info(f"\t...total parwise comparisons to be performed: {len(comparisons)}")

    # Check for existing comparisons; if one has already been done (for the same
    # software package, version, and setting) we add the comparison to this run,
    # but remove it from the list of comparisons to be performed
    logger.info("Checking database for existing comparison data...")
    comparisons_to_run = filter_existing_comparisons(
        session, run, comparisons, "blastn", blastn_version, args.fragsize, None
    )
    logger.info(
        f"\t...after check, still need to run {len(comparisons_to_run)} comparisons"
    )

    # If there are no comparisons to run, update the Run matrices and exit
    # from this function
    if not comparisons_to_run:
        logger.info("All comparison results present in database (skipping comparisons)")
        logger.info("Updating summary matrices with existing results")
        update_comparison_matrices(session, run)
        return

    # If we are in recovery mode, we are salvaging output from a previous
    # run, and do not necessarily need to rerun all the jobs. In this case,
    # we prepare a list of output files we want to recover from the results
    # in the output directory.
    if args.recovery:
        logger.warning("Entering recovery mode...")
        logger.info(
            f"\tIn this mode, existing comparison output from {args.outdir} is reused"
        )
        existingfiles = collect_existing_output(args.outdir, "blastn", args)
        logger.info(
            f"\tIdentified {len(existingfiles)} existing output files for reuse"
        )
    else:
        existingfiles = None
        logger.info(f"\tIdentified no existing output files")

    # Split the input genome files into contiguous fragments of the specified size,
    # as described in Goris et al. We create a new directory to hold sequence
    # fragments, away from the main genomes
    logger.info(f"Splitting input genome files into {args.fragsize}nt fragments...")
    fragdir = Path(args.outdir) / "fragments"
    os.makedirs(fragdir, exist_ok=True)
    fragfiles, fraglens = anib.fragment_fasta_files(
        [Path(str(_.path)) for _ in genomes],
        Path(args.outdir) / "fragments",
        args.fragsize,
    )
    logger.info(f"...wrote {len(fragfiles)} fragment files to {fragdir}")

    # Create list of BLASTN jobs for each comparison still to be performed
    logger.info("Creating blastn jobs for ANIb...")
    joblist = generate_joblist(
        comparisons_to_run, existingfiles, fragfiles, fraglens, args, logger
    )
    logger.info(f"...created {len(joblist)} blastn jobs")

    raise NotImplementedError


def generate_joblist(
    comparisons: List,
    existingfiles: List,
    fragfiles: List,
    fraglens: List,
    args: Namespace,
    logger: Logger,
) -> NotImplementedError:
    """Return list of ComparisonJobs.

    :param comparisons:  list of (Genome, Genome) tuples for which comparisons are needed
    :param existingfiles:  list of pre-existing BLASTN+ outputs
    :param fragfiles:
    :param fraglens:
    :param args:  Namespace, command-line arguments
    :param logger:  logging object
    """
    raise NotImplementedError


def fragment_fasta_file(inpath: Path, outdir: Path, fragsize: int) -> Tuple[Path, str]:
    """Return path to fragmented sequence file and JSON of fragment lengths.

    :param inpath:  Path to genome file
    :param outdir:  Path to directory to hold fragmented files
    :param fragsize:  size of genome fragments

    Returns a tuple of ``(path, json)`` where ``path`` is the path to the fragment
    file and ``json`` is a JSON-ified dictionary of fragment lengths, keyed by
    fragment sequence ID.
    """
    # Generate fragments for the input sequence, looping over each contig/
    # chromosome in the input file and breaking into sections of length
    # fragsize
    sizedict = {}
    outseqs = []
    count = 0
    for seq in SeqIO.parse(inpath, "fasta"):
        idx = 0
        while idx < len(seq):
            count += 1
            newseq = seq[idx : idx + fragsize]
            newseq.id = f"frag{count:05d}"
            outseqs.append(newseq)
            sizedict[newseq.id] = len(newseq)
            idx += fragsize

    # Write fragments to output file
    fragpath = outdir / f"{inpath.stem}-fragments.fasta"
    SeqIO.write(outseqs, fragpath, "fasta")
    return fragpath, json.dumps(sizedict)
