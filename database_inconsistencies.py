import os
import re
import sys

from darktable import darktable


# script to check for XMP rating inconsistencies.
# compares XMP ratings against your library's photo ratings.


XMP_EXT_NAME = 'xmp'
XMP_EXT = f'.{XMP_EXT_NAME}'


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


def main():
    if len(sys.argv) != 2:
        print("first argument must be the path to your Darktable config directory that contains library.db and data.db", file=sys.stderr)
        return -1
    # this opens your library's database files in read-only mode,
    # so you don't have to worry that it modifies anything.
    library = darktable.DarktableLibrary(sys.argv[1])
    photos = library.get_photos()
    count_no_rating = 0
    count_inconsistent = 0
    for photo in photos:
        database_rating = photo.rating
        if database_rating <= 1:
            continue
        xmp_rating = get_xmp_rating(photo.xmp_path)
        xmp_filename = os.path.basename(photo.xmp_path)
        if xmp_rating is None:
            print(f"no xmp rating for {photo.filepath} (version: {photo.version}, xmp: {xmp_filename}, xmp exists: {os.path.exists(photo.xmp_path)}) - database rating: {database_rating}")
            count_no_rating += 1
        if xmp_rating is not None and database_rating != xmp_rating:
            print(f"xmp rating inconsistent for {photo.filepath} (version: {photo.version}, xmp: {photo.xmp_path}): rating {database_rating} (database) vs {xmp_rating} (xmp)")
            count_inconsistent += 1
    print(f"{len(photos)} total photos in library")
    print(f"{count_no_rating} photos have no xmp file or no rating in their xmp file")
    print(f"{count_inconsistent} photos have an xmp rating that is different from their database rating")
    return 0


if __name__ == '__main__':
    sys.exit(main())
