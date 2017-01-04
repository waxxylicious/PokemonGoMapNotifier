from threading import Thread
from .utils import *
import logging
import Queue
import commentjson as json

log = logging.getLogger(__name__)


class Notifier(Thread):
    """
    Receives data from the webserver and deals with it.
    """

    def __init__(self, config_file):
        super(Notifier, self).__init__()

        self.daemon = True
        self.name = "Notifier"
        self.notification_handlers = {}
        self.processed_pokemons = {}
        self.latitude, self.longitude = None, None

        self.queue = Queue.Queue()

        with open(config_file) as file:
            log.info("Loading %s" % config_file)
            parsed = json.load(file)

            log.debug("Parsing \"config\"")
            config = parsed.get('config', {})
            self.google_key = config.get('google_key')
            self.fetch_sublocality = config.get('fetch_sublocality', False)
            self.shorten_urls = config.get('shorten_urls', False)

            log.debug("Parsing \"endpoints\"")
            self.endpoints = parsed.get('endpoints', {})
            for endpoint in self.endpoints:
                endpoint_type = self.endpoints[endpoint].get('type', 'simple')
                if endpoint_type == 'simple' and 'simple' not in self.notification_handlers:
                    log.info("Adding Simple to available notification handlers")
                    from .simple import Simple
                    self.notification_handlers['simple'] = Simple()
                if endpoint_type == 'discord' and 'discord' not in self.notification_handlers:
                    log.info("Adding Discord to available notification handlers")
                    from .discord import Discord
                    self.notification_handlers['discord'] = Discord()

            log.debug("Parsing \"includes\"")
            self.includes = parsed.get('includes', {})

            log.debug("Parsing \"notification_settings\"")
            # filter out disabled notifiers
            self.notification_settings = [notification_setting for notification_setting in
                                          parsed.get('notification_settings', []) if
                                          notification_setting.get('enabled', True)]

            for notification_setting in self.notification_settings:
                log.info("Notifying to: %s" % notification_setting.get('name', "unknown_name"))

            log.info("Parsing pokemon lists")
            self.parse_includes()
            # TODO check for circular pokemons_refs and raise errors if such exists

    def parse_includes(self):
        log.debug("Add global include config to local pokemons")

        self.add_global_to_local()
        self.resolve_refs()

        log.debug("Cleaning up refs and unused variables")
        for include in self.includes:
            self.includes[include].pop('min_iv', None)
            self.includes[include].pop('max_iv', None)
            self.includes[include].pop('min_cp', None)
            self.includes[include].pop('max_cp', None)
            self.includes[include].pop('min_attack', None)
            self.includes[include].pop('max_attack', None)
            self.includes[include].pop('min_defense', None)
            self.includes[include].pop('max_defense', None)
            self.includes[include].pop('min_stamina', None)
            self.includes[include].pop('max_stamina', None)
            self.includes[include].pop('name', None)
            self.includes[include].pop('max_dist', None)
            self.includes[include].pop('moves', None)

        for include in self.includes:
            self.includes[include] = self.includes[include]['pokemons']

        log.debug("Parsing complete")

    def resolve_refs(self):
        for include in self.includes:
            include = self.includes[include]
            if 'pokemons' not in include:
                include['pokemons'] = []

            for ref in include.get('pokemons_refs', []):
                self.add_pokemons_from_ref(ref, include)

            include.pop('pokemons_refs', None)
            self.add_global_to_local()

    def add_pokemons_from_ref(self, ref, include):
        resolved_ref = self.includes.get(ref)

        if 'pokemons' in resolved_ref:
            for pokemon in resolved_ref['pokemons']:
                include['pokemons'].append(pokemon.copy())

        if 'pokemons_refs' in resolved_ref:
            for r in resolved_ref['pokemons_refs']:
                self.add_pokemons_from_ref(r, include)

    def add_global_to_local(self):
        for include in self.includes:
            include = self.includes[include]

            for pokemon in include.get('pokemons', []):
                self.add_from_source_if_not_exists('min_iv', include, pokemon)
                self.add_from_source_if_not_exists('max_iv', include, pokemon)
                self.add_from_source_if_not_exists('min_cp', include, pokemon)
                self.add_from_source_if_not_exists('max_cp', include, pokemon)
                self.add_from_source_if_not_exists('min_attack', include, pokemon)
                self.add_from_source_if_not_exists('max_attack', include, pokemon)
                self.add_from_source_if_not_exists('min_defense', include, pokemon)
                self.add_from_source_if_not_exists('max_defense', include, pokemon)
                self.add_from_source_if_not_exists('min_stamina', include, pokemon)
                self.add_from_source_if_not_exists('max_stamina', include, pokemon)
                self.add_from_source_if_not_exists('name', include, pokemon)
                self.add_from_source_if_not_exists('max_dist', include, pokemon)
                self.add_from_source_if_not_exists('moves', include, pokemon)

    @staticmethod
    def add_from_source_if_not_exists(key, source, target):
        if key in source and key not in target:
            target[key] = source[key]

    def run(self):
        log.info("Notifier thread started.")

        while True:
            for i in range(0, 5000):
                data = self.queue.get(block=True)

                message_type = data.get('type')

                if message_type == 'pokemon':
                    self.handle_pokemon(data['message'])
                else:
                    log.debug("Unsupported message type: %s" % message_type)
            self.clean()

    def clean(self):
        now = datetime.datetime.utcnow()
        remove = []
        for encounter_id in self.processed_pokemons:
            if self.processed_pokemons[encounter_id] < now:
                remove.append(encounter_id)
        for encounter_id in remove:
            del self.processed_pokemons[encounter_id]

    @staticmethod
    def check_min(config_key, included_pokemon, message_key, pokemon):
        required_value = included_pokemon.get(config_key)
        if required_value is None:
            return True

        pokemon_value = pokemon.get(message_key, -1)
        if pokemon_value < required_value:
            return False
        return True

    @staticmethod
    def check_max(config_key, included_pokemon, message_key, pokemon):
        required_value = included_pokemon.get(config_key)
        if required_value is None:
            return True
        pokemon_value = pokemon.get(message_key, 99999)
        if pokemon_value > required_value:
            return False
        return True

    @staticmethod
    def check_min_max(key, included_pokemon, pokemon):
        """
        Returns True if included_pokemon matches the given pokemon
        :param key:
        :param included_pokemon:
        :param pokemon:
        :return:
        """
        return Notifier.check_min('min_' + key, included_pokemon, key, pokemon) and Notifier.check_max('max_' + key,
                                                                                                       included_pokemon,
                                                                                                       key,
                                                                                                       pokemon)

    def matches(self, pokemon, included_pokemon):
        # check name. if name specification doesn't exist, it counts as valid
        name = included_pokemon.get('name')
        if name is not None and name != pokemon['name']:
            return False

        # check iv
        if not Notifier.check_min_max('iv', included_pokemon, pokemon):
            return False

        # check cp
        if not Notifier.check_min_max('cp', included_pokemon, pokemon):
            return False

        # check attack
        if not Notifier.check_min_max('attack', included_pokemon, pokemon):
            return False

        # check defense
        if not Notifier.check_min_max('defense', included_pokemon, pokemon):
            return False

        # check stamina
        if not Notifier.check_min_max('stamina', included_pokemon, pokemon):
            return False

        # check distance
        max_dist = included_pokemon.get('max_dist')
        if max_dist is not None:
            if self.longitude is None or self.latitude is None:
                return False

            distance = get_distance(self.latitude, self.longitude, pokemon['lat'], pokemon['lon'])
            if distance > max_dist:
                return False

        # check moves
        if 'moves' in included_pokemon:
            moves = included_pokemon['moves']
            moves_match = False
            for move_set in moves:
                move_1 = move_set.get('move_1')
                move_2 = move_set.get('move_2')
                move_1_match = move_1 is None or move_1 == pokemon.get('move_1')
                move_2_match = move_2 is None or move_2 == pokemon.get('move_2')
                if move_1_match and move_2_match:
                    moves_match = True
                    break
            if not moves_match:
                return False

        # Passed all checks. This pokemon matches!
        return True

    def is_included_pokemon(self, pokemon, included_list):
        for included_pokemon in included_list:
            if self.matches(pokemon, included_pokemon):
                log.debug(u"Found match for {}".format(pokemon['name']))
                return True

        # Passed through all included pokemons but couldn't find a match
        log.debug(u"No match found for {}".format(pokemon['name']))
        return False

    def handle_pokemon(self, message):
        log.debug("Handling pokemon message")

        if message['encounter_id'] in self.processed_pokemons:
            log.debug("Encounter ID %s already processed.", message['encounter_id'])
            return

        self.processed_pokemons[message['encounter_id']] = datetime.datetime.utcfromtimestamp(message['disappear_time'])

        # initialize the pokemon dict
        pokemon = {
            'name': get_pokemon_name(message['pokemon_id']),
            'lat': message['latitude'],
            'lon': message['longitude']
        }

        # calculate IV if available and add corresponding values to the pokemon dict
        attack = int(message.get('individual_attack') if message.get('individual_attack') is not None else -1)
        defense = int(message.get('individual_defense') if message.get('individual_defense') is not None else -1)
        stamina = int(message.get('individual_stamina') if message.get('individual_stamina') is not None else -1)
        if attack > -1 and defense > -1 and stamina > -1:
            iv = float((attack + defense + stamina) * 100 / float(45))
            pokemon['attack'] = attack
            pokemon['defense'] = defense
            pokemon['stamina'] = stamina
            pokemon['iv'] = iv

        # add cp if available
        if 'cp' in message and message['cp'] is not None:
            pokemon['cp'] = int(message['cp'])

        # add moves to pokemon dict if found
        move_1, move_2 = None, None
        if message.get('move_1') is not None:
            move_1 = get_move_name(message['move_1'])
        if message.get('move_2') is not None:
            move_2 = get_move_name(message['move_2'])

        if move_1 is not None:
            pokemon['move_1'] = move_1
        if move_2 is not None:
            pokemon['move_2'] = move_2

        # loop through all notification settings and send notification if appropriate
        for notification_setting in self.notification_settings:
            if 'name' in notification_setting:
                log.debug("Checking through notification setting: %s" % notification_setting['name'])

            # check whether we should notify about this pokemon
            notify = False
            if 'includes' in notification_setting:
                include_refs = notification_setting['includes']
                for include_ref in include_refs:
                    include = self.includes.get(include_ref)
                    if include is None:
                        log.warn("Notification setting references unknown include: %s" % include_ref)
                        continue

                    notify = self.is_included_pokemon(pokemon, include)

            if notify:
                # find the handler and notify
                log.debug(u"Notifying about {}".format(pokemon['name']))

                lat = message['latitude']
                lon = message['longitude']
                data = {
                    'id': message['pokemon_id'],
                    'encounter_id': message['encounter_id'],
                    'time': get_disappear_time(message['disappear_time']),
                    'time_left': get_time_left(message['disappear_time']),
                    'google_maps': get_google_maps(lat, lon),
                    'static_google_maps': get_static_google_maps(lat, lon),
                    'gamepress': get_gamepress(message['pokemon_id'])
                }
                pokemon.update(data)

                # add sublocality
                if self.fetch_sublocality:
                    if not self.google_key:
                        log.warn("You must provide a google api key in order to fetch sublocality")
                    else:
                        sublocality = get_sublocality(pokemon['lat'], pokemon['lon'], self.google_key)
                        if sublocality is not None:
                            pokemon['sublocality'] = sublocality

                # now notify all endpoints
                endpoints = notification_setting.get('endpoints', ['simple'])
                for endpoint_ref in endpoints:
                    endpoint = self.endpoints[endpoint_ref]
                    notification_type = endpoint.get('type', 'simple')
                    notification_handler = self.notification_handlers[notification_type]

                    log.info(u"Notifying to endpoint {} about {}".format(endpoint_ref, pokemon['name']))
                    notification_handler.notify_pokemon(endpoint, pokemon)
            else:
                # just debug log
                if log.isEnabledFor(logging.DEBUG):
                    if 'name' in notification_setting:
                        log.debug(
                            u"Notification in {} for {} skipped".format(notification_setting['name'], pokemon['name']))
                    else:
                        log.debug(u"Notification for {} skipped".format(pokemon['name']))

    def enqueue(self, data):
        self.queue.put(data)

    def set_location(self, lat, lon):
        self.latitude = float(lat)
        self.longitude = float(lon)

        log.info("Location set to %s,%s" % (lat, lon))
