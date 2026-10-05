import requests
from tqdm import tqdm
import diskcache
import pandas as pd
import json
import time
from datetime import datetime
from typing import NamedTuple, Any
from collections import Counter
from functools import cache as ramcache
import argparse

TRANSLATION_TABLE = dict( [ (ord(x), ord(y)) for x,y in zip( u"‘’´“”–-",  u"'''\"\"--") ] ) 

SHORT_EXPIRE_S = 5*60
LONG_EXPIRE_S = 30*24*60*60

BASE_API = "https://api-web.nhle.com/"
REQUEST_DELAY_SECONDS = 1
PICKS_SHEET = "Picks.xlsx"
OUTPUT_SHEET = "Scores.xlsx"

GOALIE_POINT_MULTIPLIER = 20
GOALIE_SHUTOUT_MULTIPLIER = 10

Player = NamedTuple("Player", [("id", int), ("first_name", str), ("last_name", str), ("number", int), ("position", str), ("team", str), ("points", int)])

cache = diskcache.Cache("nhl_cache")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--input", help="Input sheet. It should have the following columns: Name, Number, Position, Team", default=PICKS_SHEET)
    parser.add_argument("-o", "--output", help="Output sheet to publish standings to.", default=OUTPUT_SHEET)
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output")
    parser.add_argument("-c", "--clear", action="store_true", help="Clear the cache")
    args = parser.parse_args()
    
    if args.clear:
        cache.clear()
    
    update_output(args.input, args.output, verbose=args.verbose)
    

def pretty_print_json(data):
    print(json.dumps(data, indent=4, sort_keys=True))

@ramcache
def request(endpoint: str) -> Any:
    if not hasattr(request, "last_request_time"):
        request.last_request_time = datetime.now()
    if (datetime.now() - request.last_request_time).total_seconds() < REQUEST_DELAY_SECONDS:
        time.sleep(REQUEST_DELAY_SECONDS - (datetime.now() - request.last_request_time).total_seconds())
    r = requests.get(BASE_API + endpoint)
    request.last_request_time = datetime.now()
    if r.status_code == 200:
        return r.json()
    else:
        print(f"Error fetching data from {endpoint}: {r.status_code}")
        return None

@cache.memoize(expire=LONG_EXPIRE_S)
def get_team_names() -> list[str]:
    response = request("v1/standings/now")
    team_names = []
    for entry in response['standings']:
        team_name = entry['teamAbbrev']['default']
        team_names.append(team_name)
    return team_names

@cache.memoize(expire=LONG_EXPIRE_S)
def get_players_from_team(team_name: str) -> list[dict]:
    r = request(f"v1/roster/{team_name}/current")
    players = []
    if r:
        response = r
        for position in ["defensemen", "forwards", "goalies"]:
            if position in response:
                for player in response[position]:
                    player_id = player['id']
                    first_name = player['firstName']['default']
                    last_name = player['lastName']['default']
                    players.append({
                        "id": player_id,
                        "first_name": first_name,
                        "last_name": last_name,
                        "team": team_name,
                        "position": position.upper()[0]})
        return players
    else:
        print(f"Error fetching player IDs for {team_name}: {r.status_code}")
        return None

@cache.memoize(expire=LONG_EXPIRE_S)
@ramcache
def get_all_players() -> pd.DataFrame:
    teams = get_team_names()
    all_players = []
    for team in tqdm(teams, desc="Finding players on teams"):
        players = get_players_from_team(team)
        all_players.extend(players)
    return pd.DataFrame(all_players)

@cache.memoize(expire=LONG_EXPIRE_S)
@ramcache
def find_player_id(**kwargs) -> int:
    df = get_all_players()
    filtered_df = df
    tried_keys = []
    for key, value in kwargs.items():
        if pd.isna(value) or key not in filtered_df:
            continue
        filtered_df = filtered_df[filtered_df[key] == value]
        if len(filtered_df) == 1:
            return filtered_df['id'].values[0]
        tried_keys.append(key)
    if len(filtered_df) > 1:
        raise ValueError(f"Multiple players found with the given criteria: {kwargs}. Tried keys: {tried_keys}")
    elif filtered_df.empty:
        print(f"No player found with the given criteria: {kwargs}")
        return 0

def find_player_info(id) -> dict:
    df = get_all_players()
    if id == 0:
        return None
    row = df[df['id'] == id]
    return row.to_dict('records')[0]

@cache.memoize(expire=SHORT_EXPIRE_S)
def get_player_stats(player_id: int) -> Any:
    r = request(f"v1/player/{player_id}/landing")
    if r:
        return r
    else:
        print(f"Error fetching stats for player {player_id}: {r.status_code}")
        return None

@cache.memoize(expire=SHORT_EXPIRE_S)
def get_skater_points() -> pd.DataFrame:
    r = request("v1/skater-stats-leaders/current?categories=points&limit=-1")
    if r:
        players = []
        points_leaders = r['points']
        for player in points_leaders:
            player_id = player['id']
            first_name = player['firstName']['default']
            last_name = player['lastName']['default']
            team = player['teamAbbrev']
            number = player['sweaterNumber']
            points = player['value']
            position = player['position'].replace("C", "F").replace("L", "F").replace("R", "F")
            players.append(Player(id=player_id, first_name=first_name, last_name=last_name, number=number, position=position, team=team, points=points))
        df = pd.DataFrame(players)
        return df
    else:
        print(f"Error fetching skater stats: {r.status_code}")
        return None

@cache.memoize(expire=SHORT_EXPIRE_S)
def get_goalie_points():
    r = request("v1/goalie-stats-leaders/current?categories=shutouts&limit=-1")
    if r:
        players = []
        points_leaders = r['shutouts']
        for player in points_leaders:
            player_id = player['id']
            first_name = player['firstName']['default']
            last_name = player['lastName']['default']
            team = player['teamAbbrev']
            number = player['sweaterNumber']
            points = player['value'] * GOALIE_SHUTOUT_MULTIPLIER
            points += get_goalie_skater_points(player_id)
            position = player['position'].replace("C", "F").replace("L", "F").replace("R", "F")
            players.append(Player(id=player_id, first_name=first_name, last_name=last_name, number=number, position=position, team=team, points=points))
        df = pd.DataFrame(players)
        return df
    else:
        print(f"Error fetching goalie stats: {r.status_code}")
        return None

@cache.memoize(SHORT_EXPIRE_S)
def get_goalie_skater_points(id) -> int:
    r = get_player_stats(id)
    currentSeason = r['seasonTotals'][-1]
    return (currentSeason['assists'] + currentSeason['goals'])*GOALIE_POINT_MULTIPLIER

@ramcache
def get_player_points() -> pd.DataFrame:
    skater_df = get_skater_points()
    goalie_df = get_goalie_points()
    return pd.concat((skater_df, goalie_df))

def get_points_and_id_from_player(id=None, **kwargs) -> tuple[int, int]:
    df = get_player_points()
    if id is None:
        id = find_player_id(**kwargs)
    row = df[df['id'] == id]
    if len(row) != 1:
        points = 0
    else:
        points = row['points'].values[0]
    return points, id

def update_output(picks_file: str=PICKS_SHEET, output_file: str=OUTPUT_SHEET, verbose=False):
    xl = pd.read_excel(picks_file, sheet_name=None)
    new_xl = {}
    player_counter = Counter()
    standings = []
    for team, sheet in xl.items():
        new_sheet = update_sheet_points(sheet)
        player_counter.update(new_sheet['Id'])
        new_xl[team] = new_sheet
    for team, sheet in new_xl.items():
        adjusted_points = []
        for row in sheet.itertuples(index=False):
            adjusted_points.append(row.Points / player_counter[row.Id])
        sheet['Adjusted Points'] = adjusted_points
        sheet.loc['Total'] = sheet.sum(numeric_only=True)
        sheet.pop('Id')
        standings.append({"Team": team, "Points": sheet['Adjusted Points']['Total']}) 
    with pd.ExcelWriter(output_file) as writer:
        standings = pd.DataFrame(standings)
        standings = standings.sort_values(by="Points", ascending=False)
        standings.to_excel(writer, sheet_name="Standings", index=False)
        for team, sheet in new_xl.items():
            sheet.to_excel(writer, sheet_name=team, index=False)
            if verbose:
                print("\n")
                print(team)
                print(sheet.to_string(index=False))
        if verbose:
            print(standings.to_string(index=False))

def update_sheet_points(df: pd.DataFrame) -> pd.DataFrame:
    output = {key: [] for key in ["Name", "Team", "Position", "Points", "Id"]}
    for row in df.itertuples(index=False):
        first_name = row.Name.split()[0].translate( TRANSLATION_TABLE )
        last_name = row.Name.split()[1].translate( TRANSLATION_TABLE )
        position = row.Position
        number = row.Number if "Number" in row else None
        team = row if "Team" in row else None
        points, id = get_points_and_id_from_player(first_name=first_name, last_name=last_name, position=position, number=number, team=team)
        info = find_player_info(id)
        team = info["team"] if team is None and info is not None and 'team' in info else team
        output["Name"].append(f"{first_name} {last_name}")
        output["Team"].append(team)
        output["Position"].append(position)
        output["Points"].append(points)
        output["Id"].append(id)
    return pd.DataFrame(output)

if __name__ == "__main__":
    main()