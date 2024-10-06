import re
import os
import subprocess
import tempfile
import datetime
import sqlite3
import string
import dateutil.parser
from enum import Enum
from pathlib import Path, PurePosixPath
from collections import defaultdict
from xml.etree import ElementTree
from xml.etree.ElementTree import Element
from io import TextIOWrapper
from os import path
from typing import Callable, Any
from PIL import Image

import exif

from darktable.util import Cache, filehash, readonly_sqlite_connection, fullname
from darktable.args_hash import args_hash


MODULE_DIR = path.abspath(path.dirname(__file__))
CACHE_FILENAME = os.path.splitext(__file__)[0] + '.cache.pkl'


Position = int


class HasId:
    id: int

    def __hash__(self):
        return self.id

    def __eq__(self, other):
        return self.id == other.id


class FilmRoll(HasId):
    def __init__(self, id, directory):
        self.id = id
        self.directory = directory

    def __repr__(self):
        return f'{self.__class__.__name__}({self.id}, {self.directory})'


class Tag(HasId):
    def __init__(self, id, name):
        self.id = id
        self.name = name

    def __repr__(self):
        return f'{self.__class__.__name__}({self.id}, {self.name})'


# https://github.com/darktable-org/darktable/blob/7b86507f/src/common/colorlabels.h#L29
class ColorLabel(Enum):
    RED = 0
    YELLOW = 1
    GREEN = 2
    BLUE = 3
    PURPLE = 4


class Photo(HasId):
    def __init__(self, id, filepath, version, datetime_taken: datetime.datetime,
                 tags: dict[Tag, Position], film_roll: FilmRoll, position: Position,
                 rating: int, color_labels: set[ColorLabel]):
        self.id: int = id
        self.filepath: str = os.path.normpath(filepath)
        self.version: int = version
        self.datetime_taken: datetime.datetime = datetime_taken
        self.tags: dict[Tag, Position] = tags
        self.film_roll: FilmRoll = film_roll
        self.position: Position = position
        self.rating: int = rating
        self.color_labels: set[ColorLabel] = color_labels

    @property
    def xmp_path(self):
        filename = path.basename(self.filepath)
        filename, ext = path.splitext(filename)
        if self.version > 0:
            filename += '_' + f'{self.version:02}'
        xmp_path = filename + ext + '.' + 'xmp'
        return path.join(path.dirname(self.filepath), xmp_path)

    def __repr__(self):
        return self.__class__.__name__ + '(' + \
            ", ".join([
                repr(self.id),
                repr(self.filepath),
                repr(self.version),
                repr(self.datetime_taken),
                repr(self.tags),
                repr(self.film_roll),
                repr(self.position),
            ]) + ')'


def parse_format_options(options_list: str):
    return list(filter(None, re.split(r'[,;\s]', options_list)))


class Export:
    def __init__(self, photo: Photo, filepath: str):
        self.photo: Photo = photo
        self.filepath: str = filepath

    @property
    def filepath(self):
        return self._filepath

    @filepath.setter
    def filepath(self, value):
        self._filepath = value
        self._width = None
        self._height = None

    @property
    def width(self):
        if self._width is None:
            self._read_export_attributes()
        return self._width

    @property
    def height(self):
        if self._height is None:
            self._read_export_attributes()
        return self._height

    @property
    def aspect_ratio(self):
        return float(self.width) / self.height

    def _read_export_attributes(self):
        with Image.open(self.filepath) as image:
            self._width, self._height = image.size

    def __repr__(self):
        return f'Export({self.filepath}, {self.photo})'


class FilenameFormat:
    """ Implements a Darktable format string for export filenames.
        Only supports a subset of variables as not all are used here.
    """

    class Placeholder:
        def __init__(self, values: list[str]):
            self.values = values

        def __getattr__(self, __name: str) -> Any:
            return FilenameFormat.Placeholder(self.values + [__name])

        def __repr__(self):
            return '.'.join(self.values).join('{}')

        def __str__(self):
            return repr(self)

    class Default(dict):
        def __missing__(self, key):
            if not key.isupper():
                raise KeyError(str(key))
            return FilenameFormat.Placeholder([key])

    def __init__(self, format_string):
        """ The format_string must be a python format string,
            where the variables from Darktable
            are transformed to python format string placeholders, e.g.:
            "$(FILE.NAME)" becomes "{FILE.NAME}".
            Not that all letters must remain uppercase.
            You can also add your own placeholders
            which will be replaced with values that are passed to render().
        """
        self.format_string = format_string

    def render(self, **kwargs):
        format_dict = FilenameFormat.Default(kwargs)
        result = string.Formatter().vformat(self.format_string, (), format_dict)
        # result = self.format_string.format_map(format_dict)
        result = re.sub(r'\{([A-Z\.]+)\}', r'$(\1)', result)
        return result


# TODO Explain how to use this class and what arguments to pass with an example
class Exporter:
    def __init__(self, *, cache_key, cli_bin, config_dir, filename_format,
                 out_ext, format_options, hq_resampling, width, height,
                 exif_artist=None, exif_copyright=None,
                 debug=False, xmp_changes=[]):
        self.cli_bin = cli_bin
        self.config_dir = config_dir
        self.filename_format = filename_format
        self.out_ext = out_ext
        self.format_options = format_options
        self.hq_resampling = hq_resampling
        self.width = width
        self.height = height
        self.exif_artist = exif_artist
        self.exif_copyright = exif_copyright
        self.debug = debug
        self.xmp_changes = xmp_changes
        fd, self.tmp_xmp_name = tempfile.mkstemp(suffix='.xmp')
        os.close(fd)

        self.args_hash = args_hash(
            cli_bin=str(cli_bin),
            config_dir=str(config_dir),
            filename_format=str(filename_format),
            out_ext=str(out_ext),
            format_options=str(format_options),
            hq_resampling=str(hq_resampling),
            width=str(width),
            height=str(height),
            xmp_changes=str([fullname(func) for func in xmp_changes])
        )
        self.cache = Cache(path.join(MODULE_DIR, CACHE_FILENAME), prefix=f'{cache_key}:main:')
        self.cache_xmp_hashes = Cache(path.join(MODULE_DIR, CACHE_FILENAME), prefix=f'{cache_key}:xmp:')
        self.cache_exported = Cache(path.join(MODULE_DIR, CACHE_FILENAME), prefix=f'{cache_key}:export:')
        if self.args_hash != self.cache.load('args_hash'):
            self.cache_exported.prune()
            self.cache_xmp_hashes.prune()
        self.cache.save('args_hash', self.args_hash)

        self._sess_exported = set()

    def __del__(self):
        os.unlink(self.tmp_xmp_name)

    def export_cached(self, photo: Photo, out_dir: str) -> Export:
        """ Exports a photo to a directory through Darktable's CLI interface,
            but only if there are changes to the XMP
            or it hasn't been exported yet.
            Returns a copy of the photo instance where export_filepath is set.
        """

        # TODO hash the class instead and return this identifier
        cache_key = f'{photo.filepath}:{photo.version}'

        xmp_hash = filehash(photo.xmp_path)
        export_filepath = self.cache_exported.load(cache_key)
        if export_filepath is not None and path.exists(export_filepath):
            self._sess_exported.add(export_filepath)
            if xmp_hash == self.cache_xmp_hashes.load(cache_key):
                return Export(photo, filepath=export_filepath)

        export = self.export(photo, out_dir=out_dir)

        self.cache_xmp_hashes.save(cache_key, xmp_hash)
        self.cache_exported.save(cache_key, export.filepath)

        return export

    def export(self, photo: Photo, out_dir: str) -> Export:
        """ Exports a photo to a directory through Darktable's CLI interface.
            Returns a copy of the photo instance where export_filepath is set.
        """

        xmp_path = photo.xmp_path

        if len(self.xmp_changes) > 0:
            with open(self.tmp_xmp_name, 'wb') as tmp_xmp_file:
                modify_xmp(xmp_path, tmp_xmp_file, changes=self.xmp_changes)
            xmp_path = self.tmp_xmp_name

        out_path = str(PurePosixPath(out_dir, self.filename_format))
        # https://docs.darktable.org/usermanual/4.0/en/special-topics/program-invocation/darktable-cli
        # https://docs.darktable.org/usermanual/4.0/en/special-topics/program-invocation/darktable
        command = [
            self.cli_bin,
            photo.filepath,
            # TODO convert any path to pure posix paths (since that's required here)
            xmp_path,
            out_path,
            # TODO if width and height is not set, don't pass it as a parameter
            f'--width', str(self.width),
            f'--height', str(self.height),
            f'--out-ext', self.out_ext,
            f'--hq', self.hq_resampling,
            f'--upscale', 'false',
            f'--apply-custom-presets', 'false',
            f'--core', # everything after this are darktable core parameters
            f'--configdir', self.config_dir,
        ]
        for option in self.format_options:
            command.append('--conf')
            command.append(f'plugins/imageio/format/{option}')

        if self.debug:
            print('xmp:', photo.xmp_path)
            print(' '.join([f"'{word}'" for word in command]))

        result = subprocess.run(command, capture_output=True, text=True)
        if self.debug:
            print(result.stdout.rstrip())

        # extract the exported filename
        match = re.search(r'exported to `([^\']+)\'', result.stdout)
        if not match:
            raise RuntimeError('expected darktable-cli output to contain filename')

        export_filepath = match.groups()[0]
        self._sess_exported.add(export_filepath)

        # save personal details in exif
        with open(export_filepath, 'rb') as image_file:
            original_exif_image = exif.Image(image_file)

        # remove exif data
        image = Image.open(export_filepath)
        data = list(image.getdata())
        image_noexif = Image.new(image.mode, image.size)
        image_noexif.putdata(data)
        image_noexif.save(export_filepath)
        image_noexif.close()

        # save personal details in exif
        with open(export_filepath, 'rb') as image_file:
            exif_image = exif.Image(image_file)
        if self.exif_artist is not None:
            exif_image.set('artist', self.exif_artist)
        if self.exif_copyright is not None:
            exif_image.set('copyright', self.exif_copyright)
        exif_image.set('datetime_original', original_exif_image.get('datetime_original'))
        with open(export_filepath, 'wb') as image_file:
            image_file.write(exif_image.get_file())

        return Export(photo, filepath=export_filepath)

    def sync(self, directory):
        """ Removes all files in the given directory, except:
            - Files that have been exported during this session and
            - Files that would have been exported but already existed.
            The current session starts at object creation
            and is reset (cleared) whenever sync() is called.
        """
        for filepath_obj in Path(directory).glob('**/*'):
            filepath = str(filepath_obj)
            if filepath_obj.is_file() and filepath not in self._sess_exported:
                if not is_raw_photo_ext(path.splitext(filepath)[1]):
                    # Remove all data associated with the photo from the cache
                    # and delete the exported photo from the directory.
                    for cache_key in self.cache_exported.keys(has_value=filepath):
                        print(f'Removed from portfolio: {cache_key}')
                        self.cache_exported.delete(cache_key)
                        self.cache_xmp_hashes.delete(cache_key)
                    try:
                        os.remove(filepath)
                    except Exception:
                        pass

        self._sess_exported.clear()


def parse_darktable_datetime(datetime_taken: int):
    # the timestamp is in microseconds
    # additionally, it uses an origin different than epoch time
    # https://github.com/darktable-org/darktable/blob/0f5bd178/src/common/datetime.c#L22C29-L22C52
    origin = dateutil.parser.isoparse('0001-01-01 00:00:00.000Z')
    epoch = dateutil.parser.isoparse('1970-01-01 00:00:00.000Z')
    epoch_delta = epoch - origin
    value = datetime_taken/1000/1000
    value = max(value, epoch_delta.total_seconds())
    value_corrected = value - epoch_delta.total_seconds()
    return datetime.datetime.fromtimestamp(value_corrected, datetime.timezone.utc)


class AttachedDatabase:
    def __init__(self, cursor: sqlite3.Cursor, name, db_path):
        self.cursor = cursor
        self.name = name
        self.db_path = db_path
        self.cursor.execute("""--sql
            ATTACH DATABASE ? AS ?;
        """, (self.db_path, self.name))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.cursor.execute("""--sql
            DETACH ?;
        """, (self.name,))


class DarktableLibrary:
    DATA_DB = 'data.db'
    LIBRARY_DB = 'library.db'

    def __init__(self, config_dir):
        self.config_dir = config_dir
        self.data_dbpath = path.join(config_dir, self.DATA_DB)
        self.library_dbpath = path.join(config_dir, self.LIBRARY_DB)
        self.data_conn = readonly_sqlite_connection(self.data_dbpath)
        self.library_conn = readonly_sqlite_connection(self.library_dbpath)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __del__(self):
        self.close()

    def close(self):
        self.data_conn.close()
        self.library_conn.close()

    def _row_to_photo(self, row: sqlite3.Row, separator: str) -> Photo:
        return Photo(
            id=int(row['id']),
            filepath=row['filepath'],
            version=int(row['version']),
            datetime_taken=parse_darktable_datetime(
                row['datetime_taken']
                if isinstance(row['datetime_taken'], int)
                else 0),
            tags={
                Tag(int(tag_id), tag_name): int(tag_position)
                for tag_id, tag_name, tag_position in zip(
                    row['tag_ids'].split(separator),
                    row['tag_names'].split(separator),
                    row['tag_positions'].split(separator)
                )
            },
            film_roll=FilmRoll(int(row['film_id']), row['film_directory']),
            position=int(row['film_position']),
            rating=row['rating'],
            color_labels=set(
                ColorLabel(int(color_label))
                for color_label in row['color_label'].split(separator)
            ),
        )

    def _select_photos(self, where_clause: str = "", args: tuple = (), limit: int = None) -> list[Photo]:
        cur = self.library_conn.cursor()
        separator = '#~~~#'
        # extracting ratings from image flags with mask 0x7:
        # https://github.com/darktable-org/darktable/blob/0f5bd178/src/common/ratings.c#L52
        # https://github.com/darktable-org/darktable/blob/0f5bd178/src/common/ratings.h#L26
        with AttachedDatabase(cur, 'data', self.data_dbpath):
            cur.execute(f"""--sql
                SELECT
                    images.id,
                    rtrim(film_rolls.folder, '/') || '/' || images.filename AS filepath,
                    images.version,
                    images.datetime_taken,
                    images.flags & 0x7 as rating,
                    film_rolls.id AS film_id,
                    film_rolls.folder AS film_directory,
                    images.position AS film_position,
                    GROUP_CONCAT(_tagged_images_2.tagid, ?) AS tag_ids,
                    GROUP_CONCAT(data.tags.name, ?) AS tag_names,
                    GROUP_CONCAT(_tagged_images_2.position, ?) AS tag_positions,
                    GROUP_CONCAT(color_labels.color, ?) as color_label
                FROM tagged_images
                INNER JOIN images ON tagged_images.imgid = images.id
                INNER JOIN film_rolls ON film_rolls.id = images.film_id
                INNER JOIN tagged_images _tagged_images_2 ON images.id = _tagged_images_2.imgid
                INNER JOIN data.tags ON _tagged_images_2.tagid = data.tags.id
                INNER JOIN color_labels ON images.id = color_labels.imgid
                {where_clause}
                GROUP BY images.id
                {f'LIMIT {limit}' if limit is not None and limit >= 0 else ''}
            """, (separator, separator, separator, separator) + args)
            result = cur.fetchall()
            return [
                self._row_to_photo(row, separator=separator)
                for row in result
            ]

    def get_photos(self) -> list[Photo]:
        return self._select_photos()

    def get_photo_by_id_and_tag(self, id: int, tag: Tag) -> Photo:
        photos = self._select_photos("""--sql
            WHERE images.id = ? AND tagged_images.tagid=?
        """, (id, tag.id), limit=1)
        return photos[0] if len(photos) > 0 else None

    def get_tag(self, tag_name) -> Tag:
        cur = self.data_conn.cursor()
        cur.execute("""--sql
            SELECT id, name
            FROM tags
            WHERE name=?
            LIMIT 1
        """, (tag_name,))
        id, name = cur.fetchone()
        return Tag(int(id), name)

    def get_tagged_photos(self, tag: Tag) -> list[Photo]:
        return self._select_photos("""--sql
            WHERE tagged_images.tagid=? AND LOWER(data.tags.name) NOT LIKE 'darktable%'
        """, (tag.id,))

    def get_subtags(self, tag_name, including_tag=False) -> list[Tag]:
        """ Returns all tag names and their tag ID
            that are underneath the given tag name in the hierarchy.
            E.g. tag_name="foo" yields "bar" for "foo|bar",
            but not "foo" if a tag is named "foo" only.
        """
        cur = self.data_conn.cursor()
        cur.execute(f"""--sql
            SELECT id, name
            FROM tags
            WHERE name LIKE ? || '|_%' {'OR name = ?' if including_tag else ''}
        """, (tag_name,) + ((tag_name,) if including_tag else ()))
        return [Tag(int(id), name) for id, name in cur.fetchall()]

    def get_photos_under_tag(self, tag_name) -> dict[Tag, list[Photo]]:
        """ Returns a dictionary of photos that are under the given tag
            in the hierarchy. The key is the subtag's name and the value
            is a tuple of the full path to the photo and its version number.
            e.g. tag_name="foo" yields "bar"->("/img.raw", 0)
            if that photo is tagged "foo|bar" in Darktable.
        """
        result = defaultdict(list)
        for tag in self.get_subtags(tag_name):
            for photo in self.get_tagged_photos(tag):
                result[tag].append(photo)
        return result


def modify_xmp(in_filename, out_fd: TextIOWrapper, changes: list[Callable[[Element, dict], None]]):
    # register all namespaces
    namespaces = dict([node for _, node in ElementTree.iterparse(in_filename, events=['start-ns'])])
    for name, uri in namespaces.items():
        ElementTree.register_namespace(name, uri)
    # parse xmp file
    tree = ElementTree.parse(in_filename)
    root = tree.getroot()
    # go through all "changers" which modify the xmp
    for func in changes:
        func(root, namespaces)
    # write output
    xmp_data = ElementTree.tostring(root, encoding='unicode')
    out_fd.seek(0)
    out_fd.truncate()
    out_fd.write(xmp_data.encode())
    out_fd.flush()


def xmp_remove_borders(xmp_root, namespaces):
    for parent in xmp_root.findall('.//darktable:history//rdf:Seq', namespaces):
        for element in parent.findall('rdf:li[@darktable:operation="borders"]', namespaces):
            key = f'{{{namespaces["darktable"]}}}enabled'
            if key in element.attrib:
                element.attrib[key] = '0'


def sanitize_xmp(in_filename, out_fd: TextIOWrapper):
    modify_xmp(in_filename, out_fd, changes=[
        xmp_remove_borders
    ])


def is_raw_photo_ext(ext: str) -> bool:
    # all raw image file extensions
    # (excluding darktable export extensions, namely tif)
    # https://en.wikipedia.org/wiki/Raw_image_format
    # https://docs.darktable.org/usermanual/4.0/en/special-topics/program-invocation/darktable-cli/
    return ext.strip().lstrip('.').lower() in set([
        '3fr', 'ari', 'arw', 'bay', 'braw', 'crw', 'cr2', 'cr3',
        'cap', 'data', 'dcs', 'dcr', 'dng', 'drf', 'eip', 'erf',
        'fff', 'gpr', 'iiq', 'k25', 'kdc', 'mdc', 'mef', 'mos',
        'mrw', 'nef', 'nrw', 'obm', 'orf', 'pef', 'ptx', 'pxn',
        'r3d', 'raf', 'raw', 'rwl', 'rw2', 'rwz', 'sr2', 'srf',
        'srw', 'tif', 'x3f'
    ]) - set(['tif'])
