import math
import time
from itertools import chain

from lutris import settings
from lutris.database import sql
from lutris.util.log import logger
from lutris.util.strings import slugify

PGA_DB = settings.PGA_DB


def get_games(
    name_filter=None,
    extra_filters=None,
    sort=None
):
    """Get the list of every game in database."""
    query = "select * from games"
    params = []
    filters = []
    if name_filter:
        filters.append("name LIKE ?")
        params.append(name_filter)
    for field in extra_filters or {}:
        if extra_filters[field]:
            filters.append("%s = ?" % field)
            params.append(extra_filters[field])
    if filters:
        query += " WHERE " + " AND ".join(filters)
    if sort:
        query += " ORDER BY %(field)s %(direction)s" % sort
    else:
        query += " ORDER BY slug ASC"
    return sql.db_query(PGA_DB, query, tuple(params))


def get_game_ids():
    """Return a list of ids of games in the database."""
    return [game["id"] for game in get_games()]


def get_games_where(**conditions):
    """
        Query games table based on conditions

        Args:
            conditions (dict): named arguments with each field matches its desired value.
            Special values for field names can be used:
                <field>__isnull will return rows where `field` is NULL if the value is True
                <field>__not will invert the condition using `!=` instead of `=`
                <field>__in will match rows for every value of `value`, which should be an iterable

        Returns:
            list: Rows matching the query

    """
    query = "select * from games"
    condition_fields = []
    condition_values = []
    for field, value in conditions.items():
        field, *extra_conditions = field.split("__")
        if extra_conditions:
            extra_condition = extra_conditions[0]
            if extra_condition == "isnull":
                condition_fields.append("{} is {} null".format(field, "" if value else "not"))
            if extra_condition == "not":
                condition_fields.append("{} != ?".format(field))
                condition_values.append(value)
            if extra_condition == "in":
                if not hasattr(value, "__iter__"):
                    raise ValueError("Value should be an iterable (%s given)" % value)
                if len(value) > 999:
                    raise ValueError("SQLite limnited to a maximum of 999 parameters.")
                if value:
                    condition_fields.append("{} in ({})".format(field, ", ".join("?" * len(value)) or ""))
                    condition_values = list(chain(condition_values, value))
        else:
            condition_fields.append("{} = ?".format(field))
            condition_values.append(value)
    condition = " AND ".join(condition_fields)
    if condition:
        query = " WHERE ".join((query, condition))
    else:
        # Inspect and document why we should return
        # an empty list when no condition is present.
        return []
    return sql.db_query(PGA_DB, query, tuple(condition_values))


def get_games_by_ids(game_ids):
    # sqlite limits the number of query parameters to 999, to
    # bypass that limitation, divide the query in chunks
    size = 999
    return list(
        chain.from_iterable(
            [
                get_games_where(id__in=list(game_ids)[page * size:page * size + size])
                for page in range(math.ceil(len(game_ids) / size))
            ]
        )
    )


def get_game_by_field(value, field="slug"):
    """Query a game based on a database field"""
    if field not in ("slug", "installer_slug", "id", "configpath", "steamid"):
        raise ValueError("Can't query by field '%s'" % field)
    game_result = sql.db_select(PGA_DB, "games", condition=(field, value))
    if game_result:
        return game_result[0]
    return {}


def get_games_by_runner(runner):
    """Return all games using a specific runner"""
    return sql.db_select(PGA_DB, "games", condition=("runner", runner))


def get_games_by_slug(slug):
    """Return all games using a specific slug"""
    return sql.db_select(PGA_DB, "games", condition=("slug", slug))


def add_game(name, **game_data):
    """Add a game to the PGA database."""
    game_data["name"] = name
    game_data["installed_at"] = int(time.time())
    if "slug" not in game_data:
        game_data["slug"] = slugify(name)
    return sql.db_insert(PGA_DB, "games", game_data)


def add_games_bulk(games):
    """
        Add a list of games to the PGA database.
        The dicts must have an identical set of keys.

        Args:
            games (list): list of games in dict format
        Returns:
            list: List of inserted game ids
    """
    return [sql.db_insert(PGA_DB, "games", game) for game in games]


def add_or_update(**params):
    """Add a game to the PGA or update an existing one

    If an 'id' is provided in the parameters then it
    will try to match it, otherwise it will try matching
    by slug, creating one when possible.
    """
    game_id = get_matching_game(params)
    if game_id:
        params["id"] = game_id
        sql.db_update(PGA_DB, "games", params, ("id", game_id))
        return game_id
    return add_game(**params)


def get_matching_game(params):
    """Tries to match given parameters with an existing game"""
    # Always match by ID if provided
    if params.get("id"):
        game = get_game_by_field(params["id"], "id")
        if game:
            return game["id"]
        logger.warning("Game ID %s provided but couldn't be matched", params["id"])
    slug = params.get("slug") or slugify(params.get("name"))
    if not slug:
        raise ValueError("Can't add or update without an identifier")
    for game in get_games_by_slug(slug):
        if game["installed"]:
            if game["configpath"] == params.get("configpath"):
                return game["id"]
        else:
            if (game["runner"] == params.get("runner") or not all([params.get("runner"), game["runner"]])):
                return game["id"]
    return None


def delete_game(game_id):
    """Delete a game from the PGA."""
    sql.db_delete(PGA_DB, "games", "id", game_id)


def set_uninstalled(game_id):
    sql.db_update(PGA_DB, "games", {"installed": 0, "runner": ""}, ("id", game_id))


def get_used_runners():
    """Return a list of the runners in use by installed games."""
    with sql.db_cursor(PGA_DB) as cursor:
        query = "select distinct runner from games where runner is not null order by runner"
        rows = cursor.execute(query)
        results = rows.fetchall()
    return [result[0] for result in results if result[0]]


def get_used_runners_game_count():
    """Return a dictionary listing for each runner in use, how many games are using it."""
    with sql.db_cursor(PGA_DB) as cursor:
        query = "select runner, count(*) from games where runner is not null group by runner order by runner"
        rows = cursor.execute(query)
        results = rows.fetchall()
    return {result[0]: result[1] for result in results if result[0]}


def get_used_platforms():
    """Return a list of platforms currently in use"""
    with sql.db_cursor(PGA_DB) as cursor:
        query = (
            "select distinct platform from games "
            "where platform is not null and platform is not '' order by platform"
        )
        rows = cursor.execute(query)
        results = rows.fetchall()
    return [result[0] for result in results if result[0]]


def get_used_platforms_game_count():
    """Return a dictionary listing for each platform in use, how many games are using it."""
    with sql.db_cursor(PGA_DB) as cursor:
        # The extra check for 'installed is 1' is needed because
        # the platform lists don't show uninstalled games, but the platform of a game
        # is remembered even after the game is uninstalled.
        query = (
            "select platform, count(*) from games "
            "where platform is not null and platform is not '' and installed is 1 "
            "group by platform "
            "order by platform"
        )
        rows = cursor.execute(query)
        results = rows.fetchall()
    return {result[0]: result[1] for result in results if result[0]}


def get_hidden_ids():
    """Return a list of game IDs to be excluded from the library view"""
    # Load the ignore string and filter out empty strings to prevent issues
    ignores_raw = settings.read_setting("library_ignores", section="lutris", default="").split(",")
    ignores = [ignore for ignore in ignores_raw if not ignore == ""]

    # Turn the strings into integers
    return [int(game_id) for game_id in ignores]


def set_hidden_ids(games):
    """Writes a list of game IDs that are to be hidden into the config file"""
    ignores_str = [str(game_id) for game_id in games]
    settings.write_setting("library_ignores", ','.join(ignores_str), section="lutris")
