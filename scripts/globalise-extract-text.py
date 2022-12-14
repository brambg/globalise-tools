#!/usr/bin/env python3
import argparse
import csv
import itertools
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from json import JSONEncoder

import pagexml.parser as pxp
import spacy
import uuid as uuid
from dataclasses_json import dataclass_json
from icecream import ic
from pagexml.model.physical_document_model import PageXMLScan, Coords
from typing import List, AnyStr, Dict, Any, Tuple

import globalise_tools.tools as gt

spacy_core = "nl_core_news_lg"

metadata_csv = "data/metadata_1618-1793_2022-08-30.csv"
ground_truth_csv = "data/globalise-word-joins-MH.csv"
textrepo_version_csv = "data/tr-versions.csv"


@dataclass_json
@dataclass
class Annotation:
    type: str
    id: str
    page_id: str
    offset: int
    length: int
    segmented_version_id: str = ""
    begin_anchor: int = 0
    end_anchor: int = 0
    txt_version_id: str = ""
    char_start: int = 0
    char_end: int = 0
    metadata: Dict[AnyStr, Any] = field(default_factory=dict)


@dataclass
class WebAnnotation:
    body: Dict[str, Any]
    target: Any

    def wrapped(self):
        anno_uuid = uuid.uuid4()
        return {
            "@context": "http://www.w3.org/ns/anno.jsonld",
            "id": f"urn:globalise:annotation:{anno_uuid}",
            "type": "Annotation",
            "motivation": "classifying",
            "generated": datetime.today().isoformat(),  # use last-modified from pagexml for px: types
            "generator": {  # use creator metadata from pagexml for px: types
                "id": "https://github.com/rvankoert/loghi-htr",
                "type": "Software",
                "name": "Loghi"
            },
            "body": self.body,
            "target": self.target
        }


@dataclass
class TRVersions:
    txt: str
    segmented: str
    conll: str


@dataclass_json
@dataclass
class GTToken:
    text: str
    text_with_ws: str
    offset: int


class AnnotationEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Annotation) \
                or isinstance(obj, gt.PXTextRegion) \
                or isinstance(obj, gt.PXTextLine) \
                or isinstance(obj, GTToken):
            return obj.to_dict()
        elif isinstance(obj, WebAnnotation):
            return obj.wrapped()
        elif isinstance(obj, Coords):
            return obj.points


metadata_records = []
ground_truth = []
iiif_base_url_idx = {}
tr_versions: Dict[str, TRVersions] = {}
nlp = None


def list_pagexml_files(directory: str):
    all_files = os.listdir(directory)
    return sorted([f'{directory}/{f}' for f in all_files if f.endswith(".xml")])


def make_id_prefix(scan_doc) -> str:
    return "urn:globalise:" + scan_doc.id.replace(".jpg", "")


def index_word_ranges(words: List[gt.DisplayWord], word_range_index) -> Dict[str, Tuple[int, int]]:
    index = {}
    for w in words:
        (range_start, range_end) = word_range_index[w.id]
        for pw in w.px_words:
            index[pw.id] = (range_start, range_end)
    return index


def process_pagexml(path):
    annotations = []
    scan_doc: PageXMLScan = pxp.parse_pagexml_file(path)
    id_prefix = make_id_prefix(scan_doc)

    px_text_regions, px_text_lines, px_words = gt.extract_px_elements(scan_doc)
    id_dispenser = gt.IdDispenser(id_prefix)
    display_words = gt.to_display_words(px_words, id_dispenser)
    text = ''
    display_word_range_idx = {}
    for w in display_words:
        stripped = w.text.strip()
        wa = word_annotation(id_prefix, stripped, text, w)
        annotations.append(wa)
        display_word_range_idx[w.id] = (wa.offset, wa.offset + wa.length)
        text += w.text
    px_word_range_idx = index_word_ranges(display_words, display_word_range_idx)

    paragraphs = [f'{p}\n' for p in text.split("\n")]
    total_size = len("".join(paragraphs))

    page_id = to_base_name(path)
    annotations.append(
        page_annotation(id_prefix, page_id, path, total_size)
    )

    for text_region in px_text_regions:
        offset = px_word_range_idx[text_region.first_word_id][0]
        last_word_range = px_word_range_idx[text_region.last_word_id]
        length = last_word_range[1] - offset
        annotations.append(
            text_region_annotation(text_region, id_prefix, offset, length)
        )

    for text_line in px_text_lines:
        offset = px_word_range_idx[text_line.first_word_id][0]
        last_word_range = px_word_range_idx[text_line.last_word_id]
        length = last_word_range[1] - offset
        annotations.append(
            text_line_annotation(text_line, id_prefix, offset, length)
        )
    return paragraphs, annotations, total_size


def text_line_annotation(text_line, id_prefix, offset, length):
    return Annotation(
        type="px:TextLine",
        id=make_textline_id(id_prefix, text_line.id),
        page_id=text_line.page_id,
        offset=offset,
        length=length,
        metadata={
            "text": text_line.text,
            "coords": text_line.coords
        }
    )


def text_region_annotation(text_region: gt.PXTextRegion, id_prefix: str, offset: int, length: int):
    return Annotation(
        type="px:TextRegion",
        id=make_textregion_id(id_prefix, text_region.id),
        page_id=text_region.page_id,
        offset=offset,
        length=length,
        metadata={
            "coords": text_region.coords,
            "text": text_region.text
        }
    )


def page_annotation(id_prefix, page_id, path, total_size):
    return Annotation(
        type="px:Page",
        id=make_page_id(id_prefix),
        page_id=page_id,
        offset=0,
        length=total_size,
        metadata={
            "n": page_id.split("_")[-1],
            "file": path,
            "na_url": gt.na_url(path),
            "tr_url": gt.tr_url(path)
        }
    )


def word_annotation(id_prefix, stripped, text, w):
    return Annotation(
        type="tt:Word",
        id=make_word_id(id_prefix, w),
        page_id=w.px_words[0].page_id,
        offset=len(text),
        length=len(stripped),
        metadata={
            "text": stripped,
            "coords": [pxw.coords for pxw in w.px_words]
        }
    )


def make_word_id(prefix: str, w) -> str:
    return prefix + ":word:" + ":".join([pxw.id for pxw in w.px_words])


def make_textline_id(prefix: str, line_id) -> str:
    return prefix + ":textline:" + line_id


def make_page_id(prefix: str) -> str:
    return prefix


def make_textregion_id(prefix: str, textregion_id) -> str:
    return prefix + ":textregion:" + textregion_id


def to_conll2002(token: str) -> str:
    return "\n" if token in ["", "\n"] else f"{token} O\n"


def as_conll2002(tokens: List[str]) -> List[str]:
    return [to_conll2002(t) for t in tokens]


def export(base_name: AnyStr,
           all_text: List[AnyStr],
           metadata: Dict[AnyStr, Any],
           tokens: List[GTToken],
           web_annotations: List[WebAnnotation]
           ):
    print(f"{base_name}:")

    file_name = f"{base_name}.txt"
    print(f"exporting text to {file_name}")
    with open(file_name, 'w', encoding='utf-8') as f:
        f.writelines(all_text)

    file_name = f"{base_name}-tokens.json"
    print(f"exporting tokens to {file_name}")
    with open(file_name, 'w', encoding='utf-8') as f:
        json.dump(tokens, f, indent=2, cls=AnnotationEncoder)

    file_name = f"{base_name}-segmented-text.json"
    print(f"exporting token segments to {file_name}")
    with open(file_name, 'w', encoding='utf-8') as f:
        segments = [t.text_with_ws if t.text_with_ws else "\n" for t in tokens]
        wrapper = {
            "_ordered_segments": segments
        }
        json.dump(wrapper, f, indent=2)

    file_name = f"{base_name}.conll"
    print(f"exporting tokens as CoNLL 2002 to {file_name}")
    token_texts = [t.text for t in tokens]
    with open(file_name, 'w', encoding='utf-8') as f:
        f.writelines(as_conll2002(token_texts))

    metadata_file_name = f"{base_name}-metadata.json"
    print(f"exporting metadata to {metadata_file_name}")
    with open(metadata_file_name, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2, cls=AnnotationEncoder)

    file_name = f"{base_name}-web-annotations.json"
    print(f"exporting web annotations to {file_name}")
    with open(file_name, 'w', encoding='utf-8') as f:
        json.dump(web_annotations, f, indent=2, cls=AnnotationEncoder)

    print()


def to_base_name(path: str) -> str:
    return path.split('/')[-1].replace(".xml", "")


def create_base_name(pagexml_files: List[str]) -> str:
    first = to_base_name(pagexml_files[0])
    last = to_base_name(pagexml_files[-1])
    i = first.rindex("_")
    base = first[0:i]
    first_page = first[i + 1:]
    last_page = last[i + 1:]
    return f"{base}_{first_page}_{last_page}"


def tokenize(all_pars: List[str]) -> (List[str], List[int]):
    tokens = []
    offsets = []
    text = ''.join(all_pars)
    doc = nlp(text)
    for sentence in doc.sents:
        for token in [t for t in sentence if t.text != "\n"]:
            tokens.append(token.text)
            offsets.append(token.idx)
        tokens.append("")
        offsets.append(-1)
    return tokens, offsets


def tokenize_per_paragraph(all_pars: List[str]) -> List[GTToken]:
    tokens = []
    text_offset = 0
    for par in all_pars:
        doc = nlp(par)
        for sentence in doc.sents:
            for token in [t for t in sentence if t.text != "\n"]:
                offset = text_offset + token.idx
                tokens.append(GTToken(token.text, token.text_with_ws, offset))
        tokens.append(GTToken("", "", -1))
        text_offset += len(par)
    return tokens


def read_metadata(basename: str) -> Dict[str, str]:
    (_a, _b, index_nr, scan_nr) = basename.split("_")
    scan = int(scan_nr)
    relevant = [r for r in metadata_records if
                r['Indexnr'] == index_nr and int(r['Scan-begin']) <= scan <= int(r['Scan-Eind'])]
    if len(relevant) > 1:
        raise ">1 metadata records relevant"
    else:
        return relevant[0]


def get_page_id(offset: int, length: int, scan_ranges) -> str:
    range_start = offset
    range_end = offset + length
    overlapping_ranges = [sr for sr in scan_ranges.items()
                          if sr[1][0] <= range_start < sr[1][1]]
    if len(overlapping_ranges) == 1:
        return overlapping_ranges[0][0]
    else:
        ic(range_start, range_end, overlapping_ranges)
        return ":placeholder:"


def make_token_annotations(base_name, tokens, scan_ranges):
    annotations = []
    par_offset = 0
    par_length = 0
    par_num = 1
    par_text = ""
    for i, gp_token in enumerate(tokens):
        token = gp_token.text
        par_text += token + " "
        offset = gp_token.offset
        token_is_paragraph_end = offset < 0
        if token_is_paragraph_end:
            page_id = get_page_id(par_offset, par_length, scan_ranges)
            annotations.append(
                paragraph_annotation(base_name, page_id, par_num, par_offset, par_length, par_text.strip()))
            par_offset += par_length
            par_num += 1
            par_text = ""
        else:
            token_length = len(token)
            page_id = get_page_id(offset, token_length, scan_ranges)
            annotations.append(
                token_annotation(base_name=base_name, page_id=page_id, token_num=i, offset=offset,
                                 token_length=token_length, token_text=token))
            par_length = offset - par_offset + token_length
        # ic(annotations[-1])
    return annotations


def paragraph_annotation(base_name: str, page_id: str, par_num: int, par_offset: int, par_length: int, text: str):
    return Annotation(
        type="tt:Paragraph",
        id=f"urn:globalise:{base_name}:paragraph:{par_num}",
        page_id=page_id,
        offset=par_offset,
        length=par_length,
        metadata={
            "text": text
        }
    )


def token_annotation(base_name, page_id, token_num, offset, token_length, token_text, sentence_num: int):
    return Annotation(
        type="tt:Token",
        id=f"urn:globalise:{base_name}:token:{token_num}",
        page_id=page_id,
        offset=offset,
        length=token_length,
        metadata={
            "text": token_text,
            "sentence_num": sentence_num,
            "token_num": token_num
        }
    )


def get_iiif_base_url(page_id: str) -> str:
    return iiif_base_url_idx[page_id]


def make_image_targets(page_id: str, coords: List[Coords]) -> List[Dict[str, Any]]:
    targets = []
    iiif_base_url = get_iiif_base_url(page_id)
    iiif_url = f"{iiif_base_url}/full/max/0/default.jpg"
    selectors = []
    for c in coords:
        xywh = f"{c.box['x']},{c.box['y']},{c.box['w']},{c.box['h']}"
        selector = {
            "type": "FragmentSelector",
            "conformsTo": "http://www.w3.org/TR/media-frags/",
            "value": f"xywh={xywh}"
        }
        selectors.append(selector)

        target = {
            "source": f"{iiif_base_url}/{xywh}/max/0/default.jpg",
            "type": "Image"
        }
        targets.append(target)

    svg_target = image_target_wth_svg_selector(iiif_url, [c.points for c in coords])
    selectors.append(svg_target['selector'])
    target = {
        "source": iiif_url,
        "type": "Image",
        "selector": selectors
    }
    targets.append(target)

    return targets


REPUBLIC_CONTEXT = "https://brambg.github.io/ns/republic.jsonld"


def make_text_targets(textrepo_base_url="", annotation: Annotation = None):
    text_anchor_selector_target = {
        'source': f"{textrepo_base_url}/rest/versions/{annotation.segmented_version_id}/contents",
        'type': "Text",
        "selector": {
            '@context': REPUBLIC_CONTEXT,
            "type": "urn:republic:TextAnchorSelector",
            "start": annotation.begin_anchor,
            "end": annotation.end_anchor
        }
    }
    cutout_target = {
        'source': f"{textrepo_base_url}/view/versions/{annotation.segmented_version_id}/segments/"
                  f"index/{annotation.begin_anchor}/{annotation.end_anchor}",
        'type': "Text"
    }
    fragment_selector_target = {
        'source': f"{textrepo_base_url}/rest/versions/{annotation.txt_version_id}/contents",
        'type': "Text",
        "selector": {
            "type": "FragmentSelector",
            "conformsTo": "http://tools.ietf.org/rfc/rfc5147",
            "value": f"char={annotation.char_start},{annotation.char_end}",
        }
    }
    return [text_anchor_selector_target, cutout_target]


def canvas_target(canvas_url: str, xywh_list: List[str] = None,
                  coords_list: List[List[Tuple[int, int]]] = None) -> dict:
    selectors = []
    if xywh_list:
        for xywh in xywh_list:
            selectors.append({
                "@context": "http://iiif.io/api/annex/openannotation/context.json",
                "type": "iiif:ImageApiSelector",
                "region": xywh
            })
    if coords_list:
        selectors.append(svg_selector(coords_list))
    return {
        '@context': "https://brambg.github.io/ns/republic.jsonld",
        'source': canvas_url,
        'type': "Canvas",
        'selector': selectors
    }


def svg_selector(coords_list):
    path_defs = []
    height = 0
    width = 0
    for coords in coords_list:
        height = max(height, max([c[1] for c in coords]))
        width = max(width, max([c[0] for c in coords]))
        path_def = ' '.join([f"L{c[0]} {c[1]}" for c in coords]) + " Z"
        path_def = 'M' + path_def[1:]
        path_defs.append(path_def)
    path = f"""<path d="{' '.join(path_defs)}"/>"""
    return {'type': "SvgSelector", 'value': f"""<svg height="{height}" width="{width}">{path}</svg>"""}


def image_target_wth_svg_selector(iiif_url: str,
                                  coords_list: List) -> dict:
    return {'source': iiif_url, 'type': "Image", 'selector': svg_selector(coords_list)}


def to_web_annotation(annotation: Annotation) -> WebAnnotation:
    body = annotation_body(annotation)
    targets = annotation_targets(annotation)
    return WebAnnotation(body=body, target=targets)


def annotation_body(annotation: Annotation):
    body = {
        "@context": {"tt": "https://brambg.github.io/ns/team-text#", "px": "https://brambg.github.io/ns/pagexml#"},
        "id": annotation.id,
        "type": annotation.type
    }
    if "text" in annotation.metadata:
        body["text"] = annotation.metadata["text"]
    return body


def to_xywh(coords: Coords):
    return f"{coords.left},{coords.top},{coords.width},{coords.height}"


def annotation_targets(annotation: Annotation):
    targets = []
    page_id = annotation.page_id
    if "coords" in annotation.metadata:
        page_id = annotation.page_id
        coords = annotation.metadata["coords"]
        if isinstance(coords, Coords):
            coords = [coords]
        targets.extend(make_image_targets(page_id, coords))
        canvas_url = f"urn:globalise:canvas:{page_id}"
        xywh_list = [to_xywh(c) for c in coords]
        points = [c.points for c in coords]
        targets.append(canvas_target(canvas_url=canvas_url, xywh_list=xywh_list, coords_list=points))
    if annotation.type == "px:Page":
        iiif_base_url = get_iiif_base_url(page_id)
        iiif_url = f"{iiif_base_url}/full/max/0/default.jpg"
        targets.append({
            "source": iiif_url,
            "type": "Image"
        })
    targets.extend(
        make_text_targets(textrepo_base_url="https://globalise.tt.di.huc.knaw.nl/textrepo",
                          annotation=annotation)
    )
    return targets


def make_web_annotations(annotations: List[Annotation]) -> List[WebAnnotation]:
    return [to_web_annotation(a) for a in annotations]


def ranges_per_scan(annotations: List[Annotation]) -> Dict[str, Tuple[int, int]]:
    return {
        pa.page_id: (pa.offset, pa.offset + pa.length)
        for pa in annotations
        if pa.type == "px:Page"
    }


def segment_range(tokens: List[GTToken], char_range_begin: int, char_range_end: int):
    begin_idx = 0
    end_idx = 0
    for i, token in enumerate(tokens):
        if -1 < token.offset <= char_range_begin:
            begin_idx = i
        if -1 < token.offset < char_range_end:
            end_idx = i
        elif -1 < token.offset:
            break
    return begin_idx, end_idx


def add_anchor_range(all_annotations: List[Annotation], tokens: List[GTToken]):
    for a in all_annotations:
        char_range_begin = a.offset
        char_range_end = a.offset + a.length
        a.begin_anchor, a.end_anchor = segment_range(tokens, char_range_begin, char_range_end)


def process_directory_group(directory_group: List[str]):
    pagexml_files = list_pagexml_files_in_group(directory_group)

    base_name = create_base_name(pagexml_files)

    all_annotations, all_pars = process_pagexml_files(pagexml_files)

    scan_ranges = ranges_per_scan(all_annotations)

    # (tokens, token_offsets) = tokenize(all_pars)
    tokens = tokenize_per_paragraph(all_pars)

    token_annotations = make_token_annotations(base_name, tokens, scan_ranges)
    all_annotations.extend(token_annotations)
    # token_selection = [a for a in all_annotations if a.type == 'tt:Token'][:5]

    add_tr_versions(all_annotations, base_name)
    add_anchor_range(all_annotations, tokens)

    all_annotations.sort(key=lambda a: f"{a.page_id} {a.offset:06d} {(1000 - a.length):06d}")

    # token_selection = [a for a in all_annotations if a.type == 'tt:Token'][:10]
    # ic(token_selection)

    metadata = read_metadata(to_base_name(pagexml_files[0]))
    metadata.update({
        "tanap_vestiging": "Batavia",
        "tanap_jaar": 1684,
        "annotations": all_annotations,
    })
    web_annotations = make_web_annotations(all_annotations)
    export(base_name, all_pars, metadata, tokens, web_annotations)


def list_pagexml_files_in_group(directory_group):
    pagexml_files = []
    for directory in directory_group:
        pagexml_files.extend(list_pagexml_files(directory))
    pagexml_files.sort()
    return pagexml_files


def process_pagexml_files(pagexml_files):
    all_pars = []
    all_annotations = []
    start_offset = 0
    for f in pagexml_files:
        (paragraphs, annotations, par_length) = process_pagexml(f)
        all_pars.extend(paragraphs)
        for annotation in annotations:
            annotation.offset += start_offset
            all_annotations.append(annotation)
        start_offset = start_offset + par_length
    return all_annotations, all_pars


def add_tr_versions(all_annotations, external_id):
    for a in all_annotations:
        a.segmented_version_id = tr_versions[external_id].segmented
        a.txt_version_id = tr_versions[external_id].txt


def init_iiif_base_url_idx(path: str):
    print(f"loading {path}...", end=' ')
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            iiif_base_url_idx[row["pagexml_id"]] = row["iiif_base_url"]
    print()


def init_spacy():
    global nlp
    nlp = spacy.load(spacy_core)


def load_metadata():
    print(f"loading {metadata_csv}...", end=' ')
    with open(metadata_csv) as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            metadata_records.append(row)
    print()


def load_ground_truth():
    print(f"loading {ground_truth_csv}...", end=' ')
    records = []
    with open(ground_truth_csv) as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append(row)
    joined_lines = [(r['scan'], r['line n'], r['line n+1']) for r in records if r['join?'] != '']
    ground_truth.extend(joined_lines)
    print()


def load_tr_versions():
    print(f"loading {textrepo_version_csv}...", end=' ')
    with open(textrepo_version_csv) as f:
        reader = csv.DictReader(f)
        for row in reader:
            tr_versions[row['external_id']] = TRVersions(txt=row['txt_version'], segmented=row['segmented_version'],
                                                         conll=row['conll_version'])
    print()


def get_arguments():
    parser = argparse.ArgumentParser(
        description="Extract text and annotations from all the PageXML in the given directory",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("-i",
                        "--iiif-mapping-file",
                        required=True,
                        help="The path to the file mapping pagexml id to iiif base url",
                        type=str)
    parser.add_argument("-m",
                        "--merge-sections",
                        required=False,
                        help="Set this to merge sections into one document",
                        action="store_true")
    parser.add_argument("directory",
                        help="A directory containing the PageXML files to extract the text from.",
                        nargs='+',
                        type=str)
    return parser.parse_args()


def process(directories, iiif_mapping_file, merge_sections):
    init_iiif_base_url_idx(iiif_mapping_file)
    init_spacy()
    load_metadata()
    load_ground_truth()
    load_tr_versions()
    if merge_sections:
        groups = itertools.groupby(directories, lambda d: d.rstrip('/').split('/')[-1].split('_')[0])
        for _, group in groups:
            process_directory_group(group)
    else:
        for d in directories:
            process_directory_group([d])


def main():
    args = get_arguments()
    if args.directory:
        process(args.directory, args.iiif_mapping_file, args.merge_sections)


if __name__ == '__main__':
    main()
