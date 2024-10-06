import os
import re
import sys
from xml.etree import ElementTree
from xml.etree.ElementTree import Element

from darktable import darktable


# script to check for XMP rating inconsistencies.
# compares XMP ratings against your library's photo ratings.
# TODO: move XMP parsing methods into darktable.py


XMP_EXT_NAME = 'xmp'
XMP_EXT = f'.{XMP_EXT_NAME}'
MIN_RATING_EXCLUDED = 1


def get_xmp_rating(file):
    _, ext = os.path.splitext(file)
    if ext != XMP_EXT:
        file = file + XMP_EXT
    if not os.path.exists(file):
        return None
    with open(file, 'r') as f:
        content = f.read()
        for match in re.findall('xmp:Rating=\"(\\d+)\"', content):
            return int(match)
    return None


def get_xmp_color_labels(file) -> set[darktable.ColorLabel]:
    namespaces = dict([node for _, node in ElementTree.iterparse(file, events=['start-ns'])])
    for name, uri in namespaces.items():
        ElementTree.register_namespace(name, uri)
    # parse xmp file
    tree = ElementTree.parse(file)
    root = tree.getroot()
    color_labels = set()
    for parent in root.findall('.//darktable:colorlabels//rdf:Seq', namespaces):
        for element in parent.findall('rdf:li', namespaces):
            color_labels.add(darktable.ColorLabel(int(element.text)))
    return color_labels


def format_color_labels(color_labels: set[darktable.ColorLabel]):
    items = list(map(lambda c: c.name.lower(),
                     sorted(list(color_labels), key=lambda c: c.value)))
    return ','.join(items) if len(items) > 0 else repr([])


def format_info(info: dict[str, any]):
    return ' '.join(
        map(lambda v: f'{v[0]}={v[1]}',
            filter(lambda v: v[1] is not None, list(info.items()))))


def main():
    if len(sys.argv) != 2:
        print("first argument must be the path to your Darktable config directory that contains library.db and data.db", file=sys.stderr)
        return -1
    # this opens your library's database files in read-only mode,
    # so you don't have to worry that it modifies anything.
    library = darktable.DarktableLibrary(sys.argv[1])
    photos = library.get_photos()
    count_checked = 0
    percent_step = 10
    next_percent = percent_step
    result_no_xmp = []
    result_no_xmp_rating = []
    result_inconsistent_xmp_rating = []
    result_inconsistent_xmp_labels = []
    print('scanning database and xmp files. this could take a while', end='', file=sys.stderr)
    for i, photo in enumerate(photos):
        percent = int(100.0 * float(i) / len(photos))
        if i % 100 == 0:
            print('.', end='', file=sys.stderr)
        if percent >= next_percent and percent < 100:
            print(f'{percent}%', end='', file=sys.stderr)
            next_percent += percent_step
        database_rating = photo.rating
        database_color_labels = photo.color_labels
        info = {
            'version': photo.version,
            'path': photo.filepath,
            'xmp': None,
            'database_rating': None,
            'xmp_rating': None,
            'database_labels': None,
            'xmp_labels': None,
        }
        if database_rating <= MIN_RATING_EXCLUDED and len(database_color_labels) == 0:
            # Hasn't been rated or marked as significant in the database
            skip = True
            if os.path.exists(photo.xmp_path):
                rating = get_xmp_rating(photo.xmp_path)
                if rating is not None and rating > MIN_RATING_EXCLUDED:
                    # this xmp has a rating that is higher than in the database
                    skip = False
                color_labels = get_xmp_color_labels(photo.xmp_path)
                if len(color_labels) > 0:
                    # this xmp has color labels which are not in the database
                    skip = False
            if skip:
                continue
        count_checked += 1
        # does the xmp file exist?
        photo_filename = os.path.basename(photo.filepath)
        if not os.path.exists(photo.xmp_path):
            result_no_xmp.append(f'{photo_filename}: no xmp file. {format_info(info)}')
            continue
        info['xmp'] = os.path.basename(photo.xmp_path)
        # does the xmp file have any rating?
        xmp_rating = get_xmp_rating(photo.xmp_path)
        if xmp_rating is None:
            copy = dict(info)
            copy['database_rating'] = database_rating
            result_no_xmp_rating.append(f'{photo_filename}: no rating in xmp file. {format_info(copy)}')
        # does the xmp file contain the correct database rating?
        if xmp_rating is not None and xmp_rating != database_rating:
            copy = dict(info)
            copy['database_rating'] = database_rating
            copy['xmp_rating'] = xmp_rating
            result_inconsistent_xmp_rating.append(f'{photo_filename}: inconsistent xmp rating. {format_info(copy)}')
        # does the xmp file contain the correct color labels?
        xmp_color_labels = get_xmp_color_labels(photo.xmp_path)
        if database_color_labels != xmp_color_labels:
            copy = dict(info)
            copy['database_labels'] = format_color_labels(database_color_labels)
            copy['xmp_labels'] = format_color_labels(xmp_color_labels)
            result_inconsistent_xmp_labels.append(f'{photo_filename}: inconsistent xmp color labels. {format_info(copy)}')

    print('100%', file=sys.stderr)
    print(file=sys.stderr)

    def print_result(label: str, result: list[str]):
        if len(result) > 0:
            print(f'{label} ({len(result)}):')
            for m in result:
                print(m)
            print()
        return len(result)

    n = 0
    n += print_result('no xmp files', result_no_xmp)
    n += print_result('no rating in xmp files', result_no_xmp_rating)
    n += print_result('xmp rating inconsistent from database rating', result_inconsistent_xmp_rating)
    n += print_result('xmp color labels inconsistent from database color labels', result_inconsistent_xmp_labels)

    print(f'{len(photos)} total photos in library')
    print(f'{count_checked} photos checked that either have a rating above {MIN_RATING_EXCLUDED} or color labels in either the database or the xmp file')
    if len(result_no_xmp) > 0:
        print(f'WARN {len(result_no_xmp)} photos have no xmp file')
    if len(result_no_xmp_rating) > 0:
        print(f'WARN {len(result_no_xmp_rating)} photos have no rating in their xmp file')
    if len(result_inconsistent_xmp_rating) > 0:
        print(f'WARN {len(result_inconsistent_xmp_rating)} photos have an xmp rating that is different from their database rating')
    if len(result_inconsistent_xmp_labels) > 0:
        print(f'WARN {len(result_inconsistent_xmp_labels)} photos have color labels that are inconsistent with their database color labels')
    if n == 0:
        print(f'GOOD your database and xmp files look consistent!')
    else:
        print(f'BAD {n} inconsistencies found.')


if __name__ == '__main__':
    main()
