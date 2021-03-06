
"""
Generate XML report of adapter statistics.
"""

import os
import logging
import sys

import numpy as np

from pbcommand.models.report import Report, Table, Column, PlotGroup, Plot
from pbcommand.common_options import add_debug_option
from pbcommand.models import FileTypes, get_pbparser
from pbcommand.cli import pbparser_runner
from pbcommand.utils import setup_log
from pbcore.io import DataSet

from pbreports.plot.helper import (get_fig_axes_lpr,
                                   save_figure_with_thumbnail, get_green)

__version__ = '0.1.0'


class Constants(object):
    TOOL_ID = "pbreports.tasks.adapter_report_xml"
    DRIVER_EXE = ("python -m pbreports.report.adapter_xml "
                  "--resolved-tool-contract ")


log = logging.getLogger(__name__)


def to_report(stats_xml, output_dir, dpi=72):
    # TODO: make dpi matter
    """Main point of entry

    :type stats_xml: str
    :type output_dir: str
    :type dpi: int

    :rtype: Report
    """
    log.info("Analyzing XML {f}".format(f=stats_xml))
    dset = DataSet(stats_xml)
    if not dset.metadata.summaryStats:
        dset.loadStats(stats_xml)
    if not dset.metadata.summaryStats.medianInsertDists:
        raise RuntimeError("No Pipeline Summary Stats (sts.xml) found")

    # Pull some stats:
    adapter_dimers = np.round(
        100.0 * dset.metadata.summaryStats.adapterDimerFraction,
        decimals=2)
    short_inserts = np.round(
        100.0 * dset.metadata.summaryStats.shortInsertFraction,
        decimals=2)

    plots = []
    # Pull some histograms (may have dupes (unmergeable distributions)):
    for i, ins_len_dist in enumerate(
            dset.metadata.summaryStats.medianInsertDists):
        # make a bar chart:
        fig, ax = get_fig_axes_lpr()
        ax.bar(map(float, ins_len_dist.labels), ins_len_dist.bins,
               color=get_green(0), edgecolor=get_green(0),
               width=(ins_len_dist.binWidth * 0.75))
        ax.set_xlabel('Median Distance Between Adapters')
        ax.set_ylabel('Reads')
        png_fn = os.path.join(output_dir,
                              "interAdapterDist{i}.png".format(i=i))
        png_base, thumbnail_base = save_figure_with_thumbnail(fig, png_fn,
                                                              dpi=dpi)

        # build the report:
        plots.append(Plot("adapter_xml_plot_{i}".format(i=i),
                          os.path.relpath(png_base, output_dir),
                          thumbnail=os.path.relpath(thumbnail_base, output_dir)))

    plot_groups = [PlotGroup("adapter_xml_plot_group",
                             title="Observed Insert Length Distribution",
                             plots=plots,
                             thumbnail=os.path.relpath(thumbnail_base, output_dir))]

    columns = [Column("adaper_xml_conditions", None,
                      ('Adapter Dimers (0-10bp)',
                       'Short Inserts (11-100bp)')),
               Column("adaper_xml_results", None,
                      (adapter_dimers, short_inserts))]

    tables = [Table("adapter_xml_table", "Adapter Statistics", columns)]

    report = Report("adapter_xml_report", title="Adapter Report",
                    tables=tables, attributes=None, plotgroups=plot_groups)

    return report


def args_runner(args):
    log.info("Starting {f} v{v}".format(f=os.path.basename(__file__),
                                        v=__version__))
    output_dir = os.path.dirname(args.report)
    report = to_report(args.subread_set, output_dir)
    report.write_json(args.report)
    return 0


def resolved_tool_contract_runner(resolved_tool_contract):
    rtc = resolved_tool_contract
    log.info("Starting {f} v{v}".format(f=os.path.basename(__file__),
                                        v=__version__))
    output_dir = os.path.dirname(rtc.task.output_files[0])
    report = to_report(rtc.task.input_files[0], output_dir)
    report.write_json(rtc.task.output_files[0])
    return 0


def _add_options_to_parser(p):
    p.add_input_file_type(
        FileTypes.DS_SUBREADS, "subread_set", "SubreadSet", "PacBio SubreadSet XML File")
    p.add_output_file_type(FileTypes.REPORT, "report", "JSON report",
                           description=("Filename of JSON output report. Should be name only, "
                                        "and will be written to output dir"),
                           default_name="report.json")


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
    return p


def _get_parser_core():
    p = get_pbparser(
        Constants.TOOL_ID,
        __version__,
        "Adapter XML Report",
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

# for 'python -m pbreports.report.sat ...'
if __name__ == "__main__":
    sys.exit(main())
