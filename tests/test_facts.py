"""Facts the knob patch depends on, checked against the stock 1.4.4 slot A bytes.

Each test names the claim it pins. They double as patch preconditions: if a
future stock image moves any of these, the patch must be re-derived.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from firmware_map import FirmwareMap
from trvzb.image import iterate_call_sites
from trvzb.symbols import Symbols, load_symbols
from trvzb.version import McuVersion


@pytest.fixture(scope="module")
def symbols() -> Symbols:
    return load_symbols()


@pytest.fixture(scope="module")
def image(stock_slot_a_path: Path) -> bytes:
    return stock_slot_a_path.read_bytes()


@pytest.fixture(scope="module")
def firmware(stock_slot_a_path: Path) -> FirmwareMap:
    return FirmwareMap(str(stock_slot_a_path))


@pytest.fixture(scope="module")
def call_targets_by_site(image: bytes) -> dict[int, int]:
    return dict(iterate_call_sites(image))


def table_bytes(image: bytes, symbols: Symbols, name: str) -> bytes:
    table = symbols.tables[name]
    return image[table.offset : table.offset + table.length]


def test_every_function_symbol__is_discovered_by_the_mapper(firmware: FirmwareMap, symbols: Symbols) -> None:
    undiscovered = {name for name, offset in symbols.function_offsets.items() if offset not in firmware.functions}
    assert not undiscovered


def test_knob_path__clamps_then_commits_the_setpoint(call_targets_by_site: dict[int, int], symbols: Symbols) -> None:
    assert call_targets_by_site[symbols.offset("ui_main_screen.knob_clamp_call")] == symbols.offset("clamp")
    assert call_targets_by_site[symbols.offset("ui_main_screen.knob_commit_call")] == symbols.offset("setpoint_set")


def test_knob_path__commits_as_a_local_change(image: bytes, symbols: Symbols) -> None:
    movs_r1_1 = bytes.fromhex("0121")
    commit_call = symbols.offset("ui_main_screen.knob_commit_call")
    assert image[commit_call - 2 : commit_call] == movs_r1_1


def test_main_screen__dispatches_events_through_switch8(call_targets_by_site: dict[int, int], symbols: Symbols) -> None:
    assert call_targets_by_site[symbols.offset("ui_main_screen.event_switch")] == symbols.offset("switch8")


def test_main_screen__is_reached_only_through_a_pointer(call_targets_by_site: dict[int, int], symbols: Symbols) -> None:
    assert symbols.offset("ui_main_screen") not in call_targets_by_site.values()


def test_valve_percent_for_decision__reads_both_valve_degrees(firmware: FirmwareMap, symbols: Symbols) -> None:
    settings_address, opening_field = symbols.field_address("settings.valve_opening_degree")
    _, closing_field = symbols.field_address("settings.valve_closing_degree")
    readers, _ = firmware.field_accesses(settings_address)
    producer = symbols.offset("valve_percent_for_decision")
    assert producer in readers[opening_field]
    assert producer in readers[closing_field]


def test_valve_percent_for_decision__is_called_only_by_the_thermostat_and_notify(
    call_targets_by_site: dict[int, int], firmware: FirmwareMap, symbols: Symbols
) -> None:
    producer = symbols.offset("valve_percent_for_decision")
    calling_functions = {firmware.function_of(site) for site, target in call_targets_by_site.items() if target == producer}
    assert calling_functions == {symbols.offset("thermostat_loop"), symbols.offset("valve_set_percent_and_notify")}


def test_datapoint_dispatch__writes_both_valve_degrees(firmware: FirmwareMap, symbols: Symbols) -> None:
    settings_address, opening_field = symbols.field_address("settings.valve_opening_degree")
    _, closing_field = symbols.field_address("settings.valve_closing_degree")
    _, writers = firmware.field_accesses(settings_address)
    assert symbols.offset("datapoint_command_dispatch") in writers[opening_field]
    assert symbols.offset("datapoint_command_dispatch") in writers[closing_field]


def test_mode_set__writes_the_mode_field(firmware: FirmwareMap, symbols: Symbols) -> None:
    settings_address, mode_field = symbols.field_address("settings.mode")
    _, writers = firmware.field_accesses(settings_address)
    assert symbols.offset("mode_set") in writers[mode_field]


def test_draw_two_characters__draws_first_argument_on_the_left(image: bytes, symbols: Symbols) -> None:
    movs_r0_2_then_movs_r0_0 = (bytes.fromhex("0220"), bytes.fromhex("0020"))
    inner = 0x324C
    assert image[inner + 6 : inner + 8] == movs_r0_2_then_movs_r0_0[0]
    assert image[inner + 0xE : inner + 0x10] == movs_r0_2_then_movs_r0_0[1]


def test_motor__uses_half_step_tables(image: bytes, symbols: Symbols) -> None:
    assert table_bytes(image, symbols, "motor_half_steps_direction_1") == bytes.fromhex("080c040602030109")
    assert table_bytes(image, symbols, "motor_half_steps_direction_2") == bytes.fromhex("0901030206040c08")


def test_slot_bases__list_both_slot_link_addresses(image: bytes, symbols: Symbols) -> None:
    assert table_bytes(image, symbols, "slot_bases") == (0x400).to_bytes(4, "little") + (0x8000).to_bytes(4, "little")


def test_digit_font__is_a_seven_segment_font(image: bytes, symbols: Symbols) -> None:
    assert table_bytes(image, symbols, "digit_font") == bytes.fromhex("3f065b4f666d7d077f6f")


def test_version_string__packs_to_the_stock_mcu_byte(image: bytes, symbols: Symbols) -> None:
    version_text = table_bytes(image, symbols, "version_string").decode()
    assert McuVersion.parse(version_text).packed() == 0x71


def test_update_path__is_inside_protected_ranges(symbols: Symbols) -> None:
    update_functions = ["uart_command_dispatch", "ota_data_block", "update_bitmap_commit", "ota_flash_write"]
    for name in update_functions:
        offset = symbols.offset(name)
        assert any(protected.overlaps(offset, 1) for protected in symbols.protected_ranges), name


def test_knob_path__is_outside_protected_ranges(symbols: Symbols) -> None:
    knob_step = symbols.offset("ui_main_screen.knob_step")
    commit_call = symbols.offset("ui_main_screen.knob_commit_call")
    assert not any(protected.overlaps(knob_step, commit_call + 4 - knob_step) for protected in symbols.protected_ranges)


@pytest.mark.parametrize("function_name", ["ui_main_screen", "draw_character"])
def test_region__covers_its_function_code_exactly(firmware: FirmwareMap, symbols: Symbols, function_name: str) -> None:
    function = firmware.functions[symbols.offset(function_name)]
    region = symbols.regions[function_name]
    assert region.start == function.address
    assert region.end >= function.end_address


def test_code_cave__is_zero_and_referenced_only_by_the_scatter_loader(image: bytes, symbols: Symbols) -> None:
    cave = symbols.regions["code_cave"]
    assert image[cave.start : cave.end] == bytes(cave.end - cave.start)
    word_offsets_pointing_into_cave = [
        offset for offset in range(0, len(image) - 3, 4)
        if cave.start + 0x400 <= int.from_bytes(image[offset : offset + 4], "little") < cave.end + 0x400
    ]
    scatter_loader_end_bound, data_init_entry_source = 0x2EC, 0x7908
    assert word_offsets_pointing_into_cave == [scatter_loader_end_bound, data_init_entry_source]


def test_code_cave__is_overwritten_by_the_vector_copy(image: bytes, symbols: Symbols) -> None:
    cave = symbols.regions["code_cave"]
    source, destination, length, _ = (int.from_bytes(image[0x7908 + 4 * index : 0x790C + 4 * index], "little") for index in range(4))
    vector_copy_word_count = 0x30
    assert (source - 0x400, destination, length) == (cave.start, 0x20000000, vector_copy_word_count * 4)


@pytest.mark.parametrize("protected_name", ["scatter_loader", "vector_copy_and_vtor", "system_reset", "uart_frame_parser", "ota_block_request", "uart_frame_send"])
def test_protected_function_range__covers_the_whole_function(firmware: FirmwareMap, symbols: Symbols, protected_name: str) -> None:
    protected = next(entry for entry in symbols.protected_ranges if entry.name == protected_name)
    function = firmware.functions[protected.start]
    assert protected.end >= function.end_address


def test_regions__never_overlap_protected_ranges(symbols: Symbols) -> None:
    for region in symbols.regions.values():
        assert not any(protected.overlaps(region.start, region.end - region.start) for protected in symbols.protected_ranges), region.name


def test_display_value_call__draws_the_pending_value(call_targets_by_site: dict[int, int], image: bytes, symbols: Symbols) -> None:
    call_site = symbols.offset("ui_main_screen.display_value_call")
    ldrb_r0_from_r5_plus_3 = bytes.fromhex("e878")
    assert image[call_site : call_site + 2] == ldrb_r0_from_r5_plus_3
    assert call_targets_by_site[call_site + 2] == symbols.offset("draw_setpoint")


def test_time_synced_set__writes_the_runtime_flag(firmware: FirmwareMap, symbols: Symbols) -> None:
    runtime_address, time_synced_field = symbols.field_address("runtime.time_synced")
    _, writers = firmware.field_accesses(runtime_address)
    assert symbols.offset("time_synced_set") in writers[time_synced_field]


def test_display_is_awake__reads_the_display_state(firmware: FirmwareMap, symbols: Symbols) -> None:
    display_state_address, awake_field = symbols.field_address("display_state.awake")
    readers, _ = firmware.field_accesses(display_state_address)
    assert symbols.offset("display_is_awake") in readers[awake_field]
