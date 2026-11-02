import asyncio
from datetime import datetime, timedelta
import json
import aiohttp
import logging

from .const import SETUP_TAG_15_ARRAY, SETUP_TAG_ARRAY, READING_TYPE_ARRAY, SETUP_TAG_BLOCKS_ARRAY
from .hourly import READING_EXPORT, READING_IMPORT
from .tariff import network_tariff_block

_LOGGER = logging.getLogger(__name__)

RATE_LIMIT_RETRIES = 5


class MojElektroError(Exception):
    """Base Moj Elektro API error."""


class MojElektroAuthError(MojElektroError):
    """Authentication or authorization failed."""


class MojElektroRequestError(MojElektroError):
    """The API rejected the request or meter identifier."""


class MojElektroConnectionError(MojElektroError):
    """The API could not be reached or returned a server error."""

class MojElektroApi:
    """Class to interact with the MojElektro API."""


    def __init__(self, token, meter_id, decimal, session):
        self.token = token
        self.meter_id = meter_id
        self.decimal = int(decimal) if decimal is not None else 4
        self.session = session
        self.cache = None
        self.cache_date = None
        self.cacheOK = False
        self.last_data = None
        self.first_load = None


    async def validate_token(self):
        """Validate token and meter access without requiring non-empty readings."""
        await self.getMeterReadings("15min")
        return True


    async def getMeterReadings(self, rType=None):
        """Fetch meter readings from the Moj Elektro API."""
        return await self._get_interval_blocks(self.define_request(rType))


    async def get_energy_intervals(self, start, end):
        """Fetch 15-minute import and export readings for [start, end).

        The API takes at most 35 days per request and answers HTTP 400 for
        dates it no longer serves.
        """
        params = "".join(
            f"&option=ReadingType%3D{reading_type}"
            for reading_type in (READING_IMPORT, READING_EXPORT)
        )
        url = (
            "https://api.informatika.si/mojelektro/v1/meter-readings"
            f"?usagePoint={self.meter_id}&startTime={start:%Y-%m-%d}"
            f"&endTime={end:%Y-%m-%d}{params}"
        )
        return await self._get_interval_blocks(url)


    async def _get_interval_blocks(self, url):
        """GET a meter-readings URL and return its interval blocks."""
        headers = {"accept": "application/json", "X-API-TOKEN": self.token}

        for attempt in range(RATE_LIMIT_RETRIES + 1):
            try:
                async with self.session.get(url, headers=headers) as response:
                    if response.status == 429 and attempt < RATE_LIMIT_RETRIES:
                        # The API allows 5 requests a second per token; the sensors
                        # and the history import can hit that together at setup.
                        wait = int(response.headers.get("RateLimit-Reset", 1))
                        _LOGGER.debug("Moj Elektro rate limit, retrying in %s s", wait)
                    else:
                        return await self._read_interval_blocks(response)
            except aiohttp.ClientError as err:
                raise MojElektroConnectionError(
                    f"Error connecting to Moj Elektro: {err}"
                ) from err
            await asyncio.sleep(wait + 1)


    async def _read_interval_blocks(self, response):
        """Turn one meter-readings response into its interval blocks."""
        if response.status == 200:
            data = await response.json()
            _LOGGER.debug("Received meter readings for %s", self.meter_id)
            interval_blocks = data.get("intervalBlocks", [])
            if not isinstance(interval_blocks, list):
                raise MojElektroRequestError(
                    "Moj Elektro returned an invalid intervalBlocks payload"
                )
            return interval_blocks

        if response.status in (401, 403):
            raise MojElektroAuthError(
                f"Moj Elektro authentication failed (HTTP {response.status})"
            )
        if response.status in (400, 404):
            raise MojElektroRequestError(
                f"Moj Elektro rejected the request (HTTP {response.status})"
            )
        raise MojElektroConnectionError(
            f"Moj Elektro API returned HTTP {response.status}"
        )


    async def getData(self):
        """Get or update cache."""

        # Update only at quaterly hour or on empty last_data
        current_minute = datetime.now().minute
        if self.last_data is None or current_minute in [0, 15, 30, 45]:

            #Update Cache
            cache = await self.getCache()

            sensor_return = {}

            if cache is None:
                _LOGGER.debug("Data not recieved! Returning empty data.")

            else:
                #validate
                self.validateData(cache)

                #organize sensors
                sensor_return.update(self.sensors_output(cache.get('15'), json.loads(SETUP_TAG_15_ARRAY)))
                sensor_return.update(self.sensors_output(cache.get('meter'), json.loads(SETUP_TAG_ARRAY)))
                
                #calc consumption by blocks
                sensor_return.update(self.consumption_by_block(cache.get('15'), json.loads(SETUP_TAG_BLOCKS_ARRAY)))
                
                # Fetch časovni blok values
                casovni_blok = await self.get_casovni_blok()
                sensor_return.update(casovni_blok)

                # Keep the entity set stable even when the API temporarily
                # omits one reading type. Preserve the previous value when
                # available; otherwise expose the entity as unavailable.
                expected_sensors = []
                for item in json.loads(SETUP_TAG_15_ARRAY):
                    expected_sensors.append(item["sensor"])
                for item in json.loads(SETUP_TAG_ARRAY):
                    expected_sensors.append(item["sensor"])
                    expected_sensors.append(
                        item["sensor"].replace("daily_", "monthly_", 1)
                    )
                for item in json.loads(SETUP_TAG_BLOCKS_ARRAY):
                    expected_sensors.append(item["sensor"])
                expected_sensors.extend(
                    f"casovni_blok_{block_number}" for block_number in range(1, 6)
                )

                for sensor_name in expected_sensors:
                    previous_value = (
                        self.last_data.get(sensor_name)
                        if self.last_data is not None
                        else None
                    )
                    sensor_return.setdefault(sensor_name, previous_value)

            self.last_data = sensor_return
            return sensor_return
        else:
            _LOGGER.debug("Not time to update, returning last good data.")
            return self.last_data


    async def getCache(self):
        """Update the API cache when necessary."""
        if self.cache is None or self.cache_date != datetime.today().date():
            _LOGGER.debug("Refreshing Moj Elektro API cache")

            meter_readings_15min = await self.getMeterReadings("15min")
            meter_readings_daily = await self.getMeterReadings()

            self.cache = {
                "15": meter_readings_15min
                if isinstance(meter_readings_15min, list)
                else [],
                "meter": meter_readings_daily
                if isinstance(meter_readings_daily, list)
                else [],
            }

            if not self.cache["15"]:
                _LOGGER.debug("No 15-minute readings returned by Moj Elektro")
            if not self.cache["meter"]:
                _LOGGER.debug("No daily meter readings returned by Moj Elektro")
        else:
            _LOGGER.debug("Using cached Moj Elektro data")

        return self.cache


    def get15MinOffset(self):
        """Get 15min index."""
        now = datetime.now()
        return int((now.hour * 60 + now.minute)/15)


    def find_tag(self, tag_to_find, data_list, search):
        """Hulahop."""
        if search == 4:
            return self._find_by_reading_type(tag_to_find, data_list)
        elif search == 1:
            return self._find_by_key(data_list, "readingType", tag_to_find, "oznaka")
        elif search == 2:
            return self._find_by_key(data_list, "oznaka", tag_to_find, "readingType")
        elif search == 3:
            return self._find_by_key(data_list, "oznaka", tag_to_find, "sensor")
        return None

    def _find_by_reading_type(self, tag_to_find, data_list):
        for index, interval_block in enumerate(data_list):
            if interval_block.get("readingType") == tag_to_find:
                return index
        return -1

    def _find_by_key(self, data_list, search_key, tag_to_find, return_key):
        for item in data_list:
            if item.get(search_key) == tag_to_find:
                return item.get(return_key)
        return None



    def define_request(self, rType = None):
        """Define url and request parameters."""
        params = ''
        if rType == '15min':
            data = json.loads(SETUP_TAG_15_ARRAY)
        else:
            data = json.loads(SETUP_TAG_ARRAY)
        for block in data:
            #construct setup
            tag = block.get("oznaka")

            #get readingTypes
            readingType = self.find_tag(tag, json.loads(READING_TYPE_ARRAY), 2)
            if readingType != None:
                #construct Url params:
                params = params + "&option=ReadingType%3D" + readingType

        current_date = datetime.now()
        self.date_to = current_date.strftime("%Y-%m-%d")
        if rType == '15min':
            self.date_from = (current_date - timedelta(days=1)).strftime("%Y-%m-%d")
            url = f'https://api.informatika.si/mojelektro/v1/meter-readings?usagePoint={self.meter_id}&startTime={self.date_from}&endTime={self.date_to}{params}'
        else:
            if current_date.day == 1:
                self.date_from = (current_date.replace(day=1) - timedelta(days=1)).replace(day=1).strftime("%Y-%m-%d")
            else:
                self.date_from = current_date.replace(day=1).strftime("%Y-%m-%d")
            url = f'https://api.informatika.si/mojelektro/v1/meter-readings?usagePoint={self.meter_id}&startTime={self.date_from}&endTime={self.date_to}{params}'
        return url


    def sensors_output(self, data, setup):
        """Organize API readings and calculate sensor values safely."""
        sensor_output = {}

        if not data:
            # Do not synthesize zeros for an empty successful response.
            # Missing keys are filled from last_data later in getData(), which
            # avoids false resets in TOTAL_INCREASING statistics.
            return sensor_output

        for block in data:
            reading_type = block.get("readingType", "")
            tag = self.find_tag(reading_type, json.loads(READING_TYPE_ARRAY), 1)
            if tag is None:
                _LOGGER.debug("Unknown reading type: %s", reading_type)
                continue

            sensor = self.find_tag(tag, setup, 3)
            if sensor is None:
                continue

            readings = block.get("intervalReadings") or []
            if not readings:
                continue

            if sensor.startswith("15min_"):
                index = self.get15MinOffset()
                if index >= len(readings):
                    _LOGGER.debug(
                        "15-minute reading index %s unavailable for %s", index, sensor
                    )
                    continue
                try:
                    sensor_output[sensor] = round(
                        float(readings[index]["value"]), self.decimal
                    )
                except (KeyError, TypeError, ValueError):
                    _LOGGER.debug("Invalid 15-minute reading for %s", sensor)
                continue

            if len(readings) < 2:
                _LOGGER.debug("Not enough daily readings for %s", sensor)
                continue

            try:
                first_value = float(readings[0]["value"])
                previous_value = float(readings[-2]["value"])
                latest_value = float(readings[-1]["value"])
            except (KeyError, TypeError, ValueError):
                _LOGGER.debug("Invalid daily readings for %s", sensor)
                continue

            sensor_output[sensor] = round(latest_value - previous_value, self.decimal)
            sensor_output[sensor.replace("daily_", "monthly_", 1)] = round(
                latest_value - first_value, self.decimal
            )

        return sensor_output


    def validateData(self, data):
        """Validate cached readings defensively before marking them current."""
        cache_15 = data.get("15") or []
        cache_meter = data.get("meter") or []
        self.cacheOK = False

        if not cache_15 or not cache_meter:
            return

        reading_types = json.loads(READING_TYPE_ARRAY)
        input_15_type = self.find_tag("A+", reading_types, 2)
        input_daily_type = self.find_tag("A+_T0", reading_types, 2)
        if input_15_type is None or input_daily_type is None:
            _LOGGER.debug("Required validation reading types are not configured")
            return

        input_15_index = self.find_tag(input_15_type, cache_15, 4)
        input_daily_index = self.find_tag(input_daily_type, cache_meter, 4)
        if input_15_index < 0 or input_daily_index < 0:
            _LOGGER.debug("Required validation reading types are missing")
            return

        readings_15 = cache_15[input_15_index].get("intervalReadings") or []
        readings_daily = cache_meter[input_daily_index].get("intervalReadings") or []
        if not readings_15 or len(readings_daily) < 2:
            return

        try:
            match_date = datetime.strptime(
                readings_15[0]["timestamp"], "%Y-%m-%dT%H:%M:%S%z"
            ).strftime("%Y-%m-%d")
            cur_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            sum_15min_part = sum(
                float(item["value"]) for item in readings_15[10:96]
            )
            sum_15min = round(
                sum(float(item["value"]) for item in readings_15[0:96]),
                self.decimal,
            )
            sum_et = round(
                float(readings_daily[-1]["value"])
                - float(readings_daily[-2]["value"]),
                self.decimal,
            )
        except (KeyError, TypeError, ValueError):
            _LOGGER.debug("Unable to validate malformed Moj Elektro readings")
            return

        if sum_15min == sum_et and sum_15min_part > 0 and cur_date == match_date:
            self.cacheOK = True
            self.cache_date = datetime.today().date()
            _LOGGER.debug("Moj Elektro cache validated successfully")
        elif cur_date != match_date:
            _LOGGER.debug(
                "Moj Elektro reading dates do not match: %s vs %s",
                cur_date,
                match_date,
            )
        else:
            _LOGGER.debug(
                "15-minute and daily data differ: %s vs %s", sum_15min, sum_et
            )


    async def get_casovni_blok(self):
        """Get currently valid contracted powers for tariff blocks."""
        url = f"https://api.informatika.si/mojelektro/v1/merilno-mesto/{self.meter_id}"
        headers = {"accept": "application/json", "X-API-TOKEN": self.token}

        try:
            response = await self._fetch_data(url, headers)
            if response:
                gsrn_omto = self._extract_gsrn_omto(response.get("merilneTocke", []))
                if gsrn_omto:
                    gsrn_data = await self._fetch_gsrn_data(gsrn_omto, headers)
                    if gsrn_data:
                        return self._extract_casovni_bloki(
                            gsrn_data.get("dogovorjeneMoci", [])
                        )
        except MojElektroAuthError:
            raise
        except MojElektroError as err:
            _LOGGER.warning("Unable to update contracted powers: %s", err)

        return {}


    async def _fetch_data(self, url, headers):
        """Fetch JSON from an auxiliary Moj Elektro endpoint."""
        try:
            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                if response.status in (401, 403):
                    raise MojElektroAuthError(
                        f"Moj Elektro authentication failed (HTTP {response.status})"
                    )
                if response.status in (400, 404):
                    raise MojElektroRequestError(
                        f"Moj Elektro rejected {url} (HTTP {response.status})"
                    )
                raise MojElektroConnectionError(
                    f"Moj Elektro API returned HTTP {response.status} for {url}"
                )
        except aiohttp.ClientError as err:
            raise MojElektroConnectionError(
                f"Error connecting to Moj Elektro: {err}"
            ) from err


    def _extract_gsrn_omto(self, merilna_mesta):
        """Extract GSRN value where 'vrsta' is 'OMTO'."""
        for mesto in merilna_mesta:
            if mesto.get('vrsta') == 'OMTO':
                return mesto.get('gsrn')
        return None

    async def _fetch_gsrn_data(self, gsrn_omto, headers):
        """Fetch GSRN data."""
        url_gsrn = f'https://api.informatika.si/mojelektro/v1/merilna-tocka/{gsrn_omto}'
        return await self._fetch_data(url_gsrn, headers)

    def _extract_casovni_bloki(self, dogovorjene_moci):
        """Extract contracted powers valid on the current date."""
        current_date = datetime.now().date()

        for moca in dogovorjene_moci:
            try:
                datum_od = datetime.strptime(
                    moca.get("datumOd"), "%Y-%m-%dT%H:%M:%S%z"
                ).date()
                datum_do = datetime.strptime(
                    moca.get("datumDo"), "%Y-%m-%dT%H:%M:%S%z"
                ).date()
            except (TypeError, ValueError):
                continue

            if moca.get("veljavnost") and datum_od <= current_date <= datum_do:
                return {
                    f"casovni_blok_{i}": moca.get(f"casovniBlok{i}")
                    for i in range(1, 6)
                }

        return {}


    def consumption_by_block(self, data, blocks):
        """Calculate daily input energy grouped by network tariff block."""
        if not data:
            return {}

        blocks_sums = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        reading_type = self.find_tag(
            "A+", json.loads(READING_TYPE_ARRAY), 2
        )
        if reading_type is None:
            return {}

        valid_reading_found = False

        for block in data:
            if block.get("readingType") != reading_type:
                continue

            for reading in block.get("intervalReadings") or []:
                try:
                    timestamp = reading["timestamp"]
                    value = float(reading["value"])
                    block_num = network_tariff_block(timestamp)
                except (KeyError, TypeError, ValueError):
                    continue

                if block_num in blocks_sums:
                    blocks_sums[block_num] += value
                    valid_reading_found = True

        if not valid_reading_found:
            return {}

        result = {}
        for mapping in blocks:
            block_num = int(mapping["oznaka"].split("_")[1])
            result[mapping["sensor"]] = round(
                blocks_sums[block_num], self.decimal
            )

        return result
