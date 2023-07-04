#!/usr/bin/env python3
import argparse

import spacy
from cassis import *
from icecream import ic
from loguru import logger
from pagexml.parser import parse_pagexml_file

typesystem_xml = 'data/typesystem.xml'
cas_xmi = "out/test-cas.xmi"

spacy_core = "nl_core_news_lg"
logger.info(f"loading {spacy_core}")
nlp = spacy.load(spacy_core)


@logger.catch
def get_arguments():
    parser = argparse.ArgumentParser(
        description="Convert a PageXML file to UAMI CAS XMI",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("page_xml_path",
                        help="The path to the pagexml file.",
                        type=str)
    return parser.parse_args()


@logger.catch
def convert(page_xml_path: str):
    scan_doc = parse_pagexml_file(page_xml_path)
    lines = []
    for tr in scan_doc.get_text_regions_in_reading_order():
        # ic(tr)
        if tr.type[-1] == "paragraph":
            for line in tr.lines:
                lines.append(line.text)
            lines.append("\n")

    text = " ".join(lines)

    logger.info(f"<= {typesystem_xml}")
    with open(typesystem_xml, 'rb') as f:
        typesystem = load_typesystem(f)

    cas = Cas(typesystem=typesystem)
    cas.sofa_string = text
    cas.sofa_mime = "text/plain"

    ic([t for t in cas.typesystem.get_types()])
    SentenceAnnotation = cas.typesystem.get_type("de.tudarmstadt.ukp.dkpro.core.api.segmentation.type.Sentence")
    TokenAnnotation = cas.typesystem.get_type("de.tudarmstadt.ukp.dkpro.core.api.segmentation.type.Token")
    doc = nlp(text)
    for sentence in doc.sents:
        cas.add(SentenceAnnotation(begin=sentence.start_char, end=sentence.end_char))
        for token in [t for t in sentence if t.text != "\n"]:
            begin = token.idx
            end = token.idx + len(token.text)
            cas.add(TokenAnnotation(begin=begin, end=end))

    # print_annotations(cas)

    logger.info(f"=> {cas_xmi}")
    cas.to_xmi(cas_xmi, pretty_print=True)


def print_annotations(cas):
    for a in cas.views[0].get_all_annotations():
        print(a)
        print(f"'{a.get_covered_text()}'")
        print()


def join_words(px_words):
    text = ""
    last_text_region = None
    last_line = None
    for w in px_words:
        if w.text_region_id == last_text_region:
            if w.line_id != last_line:
                text += "|\n"
            text += " "
        else:
            text += "\n\n"
        text += w.text
        last_text_region = w.text_region_id
        last_line = w.line_id
    return text.strip()


if __name__ == '__main__':
    args = get_arguments()
    if args.page_xml_path:
        convert(args.page_xml_path)
