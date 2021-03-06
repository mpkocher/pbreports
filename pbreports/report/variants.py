#!/usr/bin/env python

"""
Generates a table showing consensus stats and a report showing variants plots
for the top 25 contigs of the supplied reference.
"""

from collections import OrderedDict
import argparse
import logging
import os
import sys
import hashlib

import numpy as np

from pbcommand.models.report import (Table, Column, Attribute, Report,
                                     PlotGroup, Plot, PbReportError)
from pbcommand.models import TaskTypes, FileTypes, get_pbparser
from pbcommand.cli import pbparser_runner
from pbcommand.common_options import add_debug_option
from pbcommand.utils import setup_log
from pbcore.io.GffIO import GffReader

from pbreports.util import (openReference,
                            add_base_options_pbcommand,
                            get_top_contigs_from_ref_entry)
import pbreports.plot.helper as PH

log = logging.getLogger(__name__)

__version__ = '0.1'


class Constants(object):
    TOOL_ID = "pbreports.tasks.variants_report"
    DRIVER_EXE = "python -m pbreports.report.variants --resolved-tool-contract "
    MAX_CONTIGS_ID = "pbreports.task_options.max_contigs"
    MAX_CONTIGS_DEFAULT = 25
    MEAN_CONTIG_LENGTH = "mean_contig_length"
    MEAN_BASES_CALLED = "weighted_mean_bases_called"
    MEAN_CONCORDANCE = "weighted_mean_concordance"
    MEAN_COVERAGE = "weighted_mean_coverage"
    LONGEST_CONTIG = "longest_contig_name"

    ATTR_LABELS = OrderedDict([
        (MEAN_CONTIG_LENGTH, "Average Reference Length"),
        (MEAN_BASES_CALLED, "Average Reference Bases Called"),
        (MEAN_CONCORDANCE, "Average Reference Consensus Concordance"),
        (MEAN_COVERAGE, "Average Reference Coverage"),
        (LONGEST_CONTIG, "Longest Reference Contig")
    ])
    ATTR_DESCRIPTIONS = {
        MEAN_CONTIG_LENGTH: "Average length of reference sequence contigs",
        MEAN_BASES_CALLED: "Percentage of the reference sequence for which consensus bases were called",
        MEAN_CONCORDANCE: "The percent accuracy (concordance) of the consensus sequence compared to the reference",
        MEAN_COVERAGE: "The mean depth of coverage across the reference sequence",
        LONGEST_CONTIG: "The FASTA header ID of the longest reference contig",
    }


LENGTH, GAPS, ERR, COV = 0, 1, 2, 3


def make_variants_report(aln_summ_gff, variants_gff, reference, max_contigs_to_plot, report, output_dir, dpi=72, dumpdata=True):
    """
    Entry to report.
    :param aln_summ_gff: (str) path to alignment_summary.gff
    :param variants_gff: (str) path to variants_gff
    :param reference: (str) path to reference_dir
    :param max_contigs_to_plot: (int) max number of contigs to plot
    """
    _validate_inputs([('aln_summ_gff', aln_summ_gff),
                      ('variants_gff', variants_gff),
                      ('reference', reference)])

    # reference entry & top contings
    ref = openReference(reference)
    top_contigs = get_top_contigs_from_ref_entry(ref, max_contigs_to_plot)

    # extract gff data from files
    ref_data, contig_variants = _extract_alignment_summ_data(
        aln_summ_gff, top_contigs)
    _append_variants_gff_data(ref_data, variants_gff)

    # make report objects
    table, atts = _get_consensus_table_and_attributes(ref_data, ref)
    plotgroup = _create_variants_plot_grp(
        top_contigs, contig_variants, output_dir)

    rpt = Report('variants',
                 plotgroups=[plotgroup],
                 attributes=atts,
                 tables=[table])

    rpt.write_json(os.path.join(output_dir, report))
    return rpt


def _validate_inputs(files):
    """
    Raise an Error if a required file is null or non-existent
    :param files: list of tuples, first element of tuple is input name second is value
    """
    for f in files:
        if f[1] is None:
            raise PbReportError('{f} cannot be None'.format(f=f[0]))
        if not os.path.exists(f[1]):
            raise IOError('{f} does not exist'.format(f=f[1]))


def _extract_alignment_summ_data(aln_summ_gff, contigs):
    """
    :param aln_summ_gff: (str) path to alignment_summary.gff
    :param contigs: (list) top contigs from reference
    :returns: 2 dictionaries containing data extracted from alignment_summary.gff
    """

    def _get_name(id_):
        for c in contigs:
            if c.id == id_:
                return c.name

    contig_ids = [c.id for c in contigs]

    ref_data = {}
    var_map = {}

    log.info("Reading GFF data from {f}".format(f=aln_summ_gff))

    reader = GffReader(aln_summ_gff)
    for rec in reader:
        seqid = rec.seqid.split()[0]
        if seqid not in contig_ids:
            continue

        # first data set
        ref_data.setdefault(seqid, [0, 0, 0, 0])
        ref_data[seqid][LENGTH] = max(rec.end, ref_data[seqid][LENGTH])
        numGaps, lenGaps = rec.attributes["gaps"].split(",")
        ref_data[seqid][GAPS] += int(lenGaps)
        ref_data[seqid][COV] += float( rec.attributes["cov2"].split(",")[0] ) * \
            (rec.end - rec.start + 1)

        # second data set
        contig_var = None
        try:
            contig_var = var_map[seqid]
        except KeyError:
            contig_var = ContigVariants(seqid, _get_name(seqid))
            var_map[seqid] = contig_var

        contig_var.add_data(rec)

    reader.close()

    return ref_data, var_map


def _create_variants_plot_grp(top_contigs, var_map, output_dir):
    """
    Returns io.model.PlotGroup object
    Create the plotGroup element that contains variants plots of the top contigs.
    :param top_contigs: (list of Contig objects) sorted by contig size
    :param var_map: (dict string:ContigVariants) mapping of contig.header to ContigVariants object
    :param output_dir: (string) where to write images
    """
    plots = []
    thumbnail = None
    legend = None
    idx = 0
    for tc in top_contigs:
        if not tc.header in var_map:
            # no coverage of this contig
            continue
        ctg_var = var_map[tc.header]
        bars = _create_bars(ctg_var)
        if legend is None:
            legend = _get_legend_file(bars, output_dir)

        fig, ax = _create_contig_fig_ax(bars, _get_x_labels(ctg_var))

        fname = os.path.join(output_dir, ctg_var.file_name)
        if thumbnail is None:
            imgfiles = PH.save_figure_with_thumbnail(fig, fname)
            thumbnail = os.path.basename(imgfiles[1])
        else:
            fig.savefig(fname)

        id_ = 'coverage_variants_{i}'.format(i=str(idx))
        caption = "Observed variants across {c}".format(c=ctg_var.name)
        plot = Plot(id_, os.path.basename(fname), caption)
        plots.append(plot)
        idx += 1

    plot_group = PlotGroup('variants_plots', title='Variants Across Reference', legend=legend,
                           thumbnail=thumbnail, plots=plots)
    return plot_group


def _get_x_labels(ctg_var):
    return np.array([l[0] for l in ctg_var.variants])


def _get_legend_file(bars, output_dir):
    """
    Get the legend basename
    :param bars: iterable pbreports.plot.helper.Bar
    :param output_dir: Where to write file
    :return (string) filename
    """
    fig = PH.get_bar_plot_legend_fig(bars)
    fname = 'variants_plot_legend.png'
    fig.savefig(os.path.join(output_dir, fname), dpi=60)
    return fname


def _create_bars(contig_variants):
    """
    :param contig_variants: (ContigVariants)
    :returns: tuple of pbreports.plot.helper.Bar objects
    """

    dataIns = np.array([l[1] for l in contig_variants.variants])
    dataDels = np.array([l[2] for l in contig_variants.variants])
    dataSnv = np.array([l[3] for l in contig_variants.variants])

    insBarModel = PH.Bar(dataIns, 'Insertions', color=PH.get_blue(3))
    delBarModel = PH.Bar(dataDels, 'Deletions', color=PH.get_green(3))
    snvBarModel = PH.Bar(dataSnv, 'Substitutions', color=PH.get_orange())

    return (insBarModel, delBarModel, snvBarModel)


def _create_contig_fig_ax(bars, xlabels):
    """
    Returns a fig,ax plot for this contig
    :param contig_variants: (ContigVariants) 
    """
    fig, ax = PH.get_fig_axes_lpr()
    PH.apply_bar_data(
        ax, bars, xlabels, ('Reference Start Position', 'Variants'))
    return fig, ax


def _append_variants_gff_data(ref_data, variants_gff):
    """
    Adds data from variants gff to the ref_data dict
    :param ref_data: (dict) dict of data pulled from alignment_summary.gff
    :param variants_gff: (str) path to variants_gff

    :type variants_gff: str
    """
    reader = GffReader(variants_gff)
    for record in reader:
        err_len = record.end - record.start + 1
        seqid = record.seqid.split()[0]
        if seqid in ref_data:
            ref_data[seqid][ERR] += err_len
        else:
            # the variants might not be present in the top 25 contigs,
            # so we can just raise a warning in the log.
            msg = "Unable to find {r} in {f}".format(
                r=seqid, f=variants_gff)
            log.warn(msg)

    reader.close()


def _get_consensus_table_and_attributes(ref_data, reference_entry):
    """
    Get a tuple: Table and list of Attributes
    :param ref_data: (dict) dict of data pulled from alignment_summary.gff
    :param reference_entry: reference entry
    :return: tuple (Table, [Attributes])
    """
    table = Table('consensus_table', 'Consensus Calling Results')
    table.add_column(Column('contig_name', 'Reference'))
    table.add_column(Column('contig_len', 'Reference Length'))
    table.add_column(Column('bases_called', 'Bases Called'))
    table.add_column(Column('concordance', 'Consensus Accuracy'))
    table.add_column(Column('coverage', 'Base Coverage'))

    ordered_ids = _ref_ids_ordered_by_len(ref_data)

    sum_lengths = 0.0
    mean_bases_called = 0
    mean_concord = 'NA'
    mean_coverage = 0

    for seqid in ordered_ids:
        contig = reference_entry.get_contig(seqid)

        length = float(ref_data[seqid][LENGTH])
        gaps = float(ref_data[seqid][GAPS])
        errors = float(ref_data[seqid][ERR])
        cov = float(ref_data[seqid][COV])

        sum_lengths += length
        bases_called = 1.0 - gaps / length
        mean_bases_called += bases_called * length

        concord = 'NA'
        if length != gaps:

            log.info('length {f}'.format(f=length))
            log.info('gaps {f}'.format(f=gaps))
            log.info('errors {f}'.format(f=errors))

            concord = 1.0 - errors / (length - gaps)
            if mean_concord is 'NA':
                mean_concord = concord * length
            else:
                mean_concord += concord * length

        coverage = cov / length
        mean_coverage += coverage * length

        # table shows values for each contig
        table.add_data_by_column_id('contig_name', contig.name)
        table.add_data_by_column_id('contig_len', length)
        table.add_data_by_column_id('bases_called', bases_called)
        table.add_data_by_column_id('concordance', concord)
        table.add_data_by_column_id('coverage', coverage)

    mean_contig_length = sum_lengths / len(ordered_ids)
    mean_bases_called = mean_bases_called / sum_lengths
    if mean_concord is not 'NA':
        mean_concord = mean_concord / sum_lengths
    mean_coverage = mean_coverage / sum_lengths

    attributes = [Attribute(id_, val, Constants.ATTR_LABELS[id_])
                  for id_, val in [
        (Constants.MEAN_CONTIG_LENGTH, mean_contig_length),
        (Constants.MEAN_BASES_CALLED, mean_bases_called),
        (Constants.MEAN_CONCORDANCE, mean_concord),
        (Constants.MEAN_COVERAGE, mean_coverage),
        (Constants.LONGEST_CONTIG, ordered_ids[0])]]

    return table, attributes


def _ref_ids_ordered_by_len(ref_data):
    """
    Returns a list of seq id strings, ordered by the length of the sequence
    :param ref_data: (dict) dict of data pulled from alignment_summary.gff
    "return: list
    """
    ordered_tuples = []
    for ref in ref_data.keys():
        ordered_tuples.append((ref, ref_data[ref][LENGTH]))
    ordered_tuples = sorted(
        ordered_tuples, key=lambda tup: tup[1], reverse=True)
    return [i[0] for i in ordered_tuples]


class ContigVariants(object):

    def __init__(self, seqId, name=None):
        """Encapsulates variant info relevant to one chart"""
        self.seqid = seqId

        self.name = seqId if name is None else name

        self.variants = []

        # seqId is the fasta header, which could be long and have spaces and/or symbols that are
        # not good to use in filename.
        m = hashlib.md5()
        m.update(seqId)

        self.file_name = "variants_plot_%s%s" % (m.hexdigest(), ".png")

    def add_data(self, gff3Record):
        """Append x,y data from this record to the contig graph"""

        atts = gff3Record.attributes
        startPos = int(gff3Record.start)
        inse = int(atts['ins'])
        de1e = int(atts['del'])
        snv = int(atts['sub'])

        self.variants.append((startPos, inse, de1e, snv))


def args_runner(args):
    rpt = make_variants_report(args.aln_summ_gff, args.variants_gff, args.reference, args.maxContigs,
                               args.report, args.output)
    log.info(rpt)
    return 0


def resolved_tool_contract_runner(resolved_tool_contract):
    rtc = resolved_tool_contract
    rpt = make_variants_report(
        aln_summ_gff=rtc.task.input_files[1],
        variants_gff=rtc.task.input_files[2],
        reference=rtc.task.input_files[0],
        max_contigs_to_plot=rtc.task.options[Constants.MAX_CONTIGS_ID],
        report=rtc.task.output_files[0],
        output_dir=os.path.dirname(rtc.task.output_files[0]))
    log.info(rpt)
    return 0


def _add_options_to_parser(p):
    p = add_base_options_pbcommand(p)
    p.add_input_file_type(FileTypes.DS_REF,
                          file_id="reference",
                          name="Reference dataset",
                          description="ReferenceSet or FASTA")
    p.add_input_file_type(FileTypes.GFF,
                          file_id="aln_summ_gff",
                          name="Alignment summary GFF",
                          description="Alignment summary GFF")
    p.add_input_file_type(FileTypes.GFF,
                          file_id="variants_gff",
                          name="Variants GFF",
                          description="Variants GFF")
    p.add_int(Constants.MAX_CONTIGS_ID, "maxContigs",
              default=Constants.MAX_CONTIGS_DEFAULT,
              name="Max contigs",
              description="Max number of contigs to plot. Defaults to 25.")
    return p


def add_options_to_parser(p):
    """
    API function for extending main pbreport arg parser (independently of
    tool contract interface).
    """
    p_wrap = _get_parser_core()
    p_wrap.arg_parser.parser = p
    p.description = __doc__
    add_debug_option(p)
    _add_options_to_parser(p_wrap)
    p.set_defaults(func=args_runner)
    return p


def _get_parser_core():
    p = get_pbparser(
        Constants.TOOL_ID,
        __version__,
        "Variants Report",
        __doc__,
        Constants.DRIVER_EXE,
        is_distributed=True)

    return p


def get_parser():
    p = _get_parser_core()
    _add_options_to_parser(p)
    return p


def main(argv=sys.argv):
    mp = get_parser()
    return pbparser_runner(argv[1:],
                           mp,
                           args_runner,
                           resolved_tool_contract_runner,
                           log,
                           setup_log)

if __name__ == "__main__":
    sys.exit(main())
