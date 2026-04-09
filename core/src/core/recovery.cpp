#include "../../include/core/recovery.hpp"
#include "../../include/core/hive.hpp"
#include "../../include/core/decoder.hpp"
#include <cstring>
#include <cmath>
#include <set>

namespace regbroker::core {

RecoveryEngine::RecoveryEngine(const Hive& hive) : hive_(hive) {}

// ── Validation heuristics ─────────────────────────────────────────────────────

bool RecoveryEngine::is_valid_nk_candidate(const NkCell* nk, uint32_t cell_size) const {
    // Sanity checks on the NK fields
    if (cell_size < sizeof(NkCell)) return false;
    if (nk->key_name_len == 0 || nk->key_name_len > 512) return false;
    if (sizeof(NkCell) + nk->key_name_len > cell_size) return false;

    // Flags must be a reasonable combination
    const uint16_t VALID_FLAGS = 0x03FF;
    if (nk->flags & ~VALID_FLAGS) return false;

    // Timestamp should be in a valid Windows era (after 1995, before 2040)
    // FILETIME for 1995-01-01: ~125000000000000000
    // FILETIME for 2040-01-01: ~132000000000000000
    if (nk->timestamp != 0 &&
        (nk->timestamp < 125000000000000000ULL || nk->timestamp > 135000000000000000ULL)) {
        return false;
    }
    return true;
}

bool RecoveryEngine::is_valid_vk_candidate(const VkCell* vk, uint32_t cell_size) const {
    if (cell_size < sizeof(VkCell)) return false;
    if (vk->value_name_len > 16383) return false;
    if (sizeof(VkCell) + vk->value_name_len > cell_size) return false;

    uint32_t dtype = vk->data_type;
    if (dtype > 11 && dtype != 0xFFFFFFFF) return false;

    return true;
}

bool RecoveryEngine::is_offset_reachable(uint32_t offset) const {
    return hive_.is_allocated_cell(offset);
}

// ── Per-cell recovery attempt ─────────────────────────────────────────────────

bool RecoveryEngine::try_recover_nk(uint32_t cell_offset, uint32_t hbin_offset,
                                     RecoveryReport& report) {
    const NkCell* nk = hive_.cell_ptr<NkCell>(cell_offset);
    if (!nk) return false;
    if (nk->signature[0] != 'n' || nk->signature[1] != 'k') return false;

    // Cell size must be positive (free) for recovery
    // (actually the header holds size: if the calling code already confirmed it's free,
    //  we just validate the content)
    uint32_t abs_size = static_cast<uint32_t>(std::abs(nk->cell_size));
    if (!is_valid_nk_candidate(nk, abs_size)) return false;

    // Build a recovered key
    // Read name (may be partially overwritten)
    const uint8_t* name_ptr = reinterpret_cast<const uint8_t*>(nk) + sizeof(NkCell);
    std::string name;
    if (nk->flags & NK_ASCII_NAME) {
        name = std::string(reinterpret_cast<const char*>(name_ptr), nk->key_name_len);
    } else {
        for (size_t i = 0; i + 1 < nk->key_name_len; i += 2) {
            uint16_t wc = name_ptr[i] | (name_ptr[i+1] << 8);
            name += (wc >= 0x20 && wc < 0x7F) ? static_cast<char>(wc) : '?';
        }
    }

    RegKey key;
    key.name        = name;
    key.path        = "\\(recovered)\\" + name;
    key.timestamp   = nk->timestamp;
    key.cell_offset = cell_offset;
    key.file_offset = HIVE_DATA_START + cell_offset;
    key.flags       = nk->flags;
    key.num_values  = nk->num_values;
    key.num_subkeys = nk->num_subkeys;
    key.deleted     = true;

    RecoveredKey rk;
    rk.key              = key;
    rk.cell_offset      = cell_offset;
    rk.hbin_offset      = hbin_offset;
    rk.parent_reachable = is_offset_reachable(nk->parent_offset);
    rk.reason           = "NK signature in free cell; valid flags, timestamp, name length";
    report.keys.push_back(std::move(rk));
    return true;
}

bool RecoveryEngine::try_recover_vk(uint32_t cell_offset, uint32_t hbin_offset,
                                     RecoveryReport& report) {
    const VkCell* vk = hive_.cell_ptr<VkCell>(cell_offset);
    if (!vk) return false;
    if (vk->signature[0] != 'v' || vk->signature[1] != 'k') return false;

    uint32_t abs_size = static_cast<uint32_t>(std::abs(vk->cell_size));
    if (!is_valid_vk_candidate(vk, abs_size)) return false;

    // Read name
    std::string name;
    if (vk->value_name_len > 0) {
        const uint8_t* name_ptr = reinterpret_cast<const uint8_t*>(vk) + sizeof(VkCell);
        if (vk->flags & VK_ASCII_NAME) {
            name = std::string(reinterpret_cast<const char*>(name_ptr), vk->value_name_len);
        } else {
            for (size_t i = 0; i + 1 < vk->value_name_len; i += 2) {
                uint16_t wc = name_ptr[i] | (name_ptr[i+1] << 8);
                name += (wc >= 0x20 && wc < 0x7F) ? static_cast<char>(wc) : '?';
            }
        }
    }

    RegValue val;
    val.name        = name;
    val.type        = static_cast<ValueType>(vk->data_type);
    val.cell_offset = cell_offset;
    val.deleted     = true;

    // Try to read data (may or may not still be intact)
    bool data_intact = false;
    uint32_t raw_size = vk->data_size;
    bool inline_data  = (raw_size & 0x80000000) != 0;
    uint32_t data_size = raw_size & 0x7FFFFFFF;

    if (inline_data && data_size <= 4) {
        const uint8_t* ptr = reinterpret_cast<const uint8_t*>(&vk->data_offset);
        val.data.assign(ptr, ptr + data_size);
        data_intact = true;
    } else if (!inline_data && vk->data_offset != INVALID_OFFSET && data_size < 1024*1024) {
        uint64_t doff = HIVE_DATA_START + vk->data_offset;
        if (doff + 4 + data_size <= hive_.data_size()) {
            const uint8_t* ptr = hive_.data() + doff + 4;
            val.data.assign(ptr, ptr + data_size);
            data_intact = true;
        }
    }

    RecoveredValue rv;
    rv.value       = val;
    rv.cell_offset = cell_offset;
    rv.hbin_offset = hbin_offset;
    rv.data_intact = data_intact;
    rv.reason      = "VK signature in free cell; valid type, name length";
    report.values.push_back(std::move(rv));
    return true;
}

// ── Main scan ─────────────────────────────────────────────────────────────────

RecoveryReport RecoveryEngine::scan() {
    return scan_partial(SIZE_MAX);
}

RecoveryReport RecoveryEngine::scan_partial(size_t max_hbins) {
    RecoveryReport report;
    const uint8_t* buf  = hive_.data();
    size_t         size = hive_.data_size();

    if (size < HIVE_DATA_START + sizeof(HbinHeader)) return report;

    size_t hbin_count = 0;
    size_t hbin_pos   = HIVE_DATA_START;

    while (hbin_pos + sizeof(HbinHeader) <= size && hbin_count < max_hbins) {
        const HbinHeader* hbin = reinterpret_cast<const HbinHeader*>(buf + hbin_pos);
        if (std::memcmp(hbin->signature, "hbin", 4) != 0) {
            // Possibly corrupted; try to advance by page size
            hbin_pos += 4096;
            continue;
        }

        uint32_t hbin_size = hbin->size;
        if (hbin_size < sizeof(HbinHeader) || hbin_pos + hbin_size > size) break;

        ++hbin_count;
        uint32_t hbin_offset = static_cast<uint32_t>(hbin_pos - HIVE_DATA_START);

        // Scan cells within this HBIN
        size_t cell_pos = hbin_pos + sizeof(HbinHeader);
        while (cell_pos + 4 <= hbin_pos + hbin_size) {
            int32_t cell_sz;
            std::memcpy(&cell_sz, buf + cell_pos, 4);

            if (cell_sz == 0) break;  // should not happen
            uint32_t abs_sz = static_cast<uint32_t>(std::abs(cell_sz));
            if (abs_sz < 8 || cell_pos + abs_sz > hbin_pos + hbin_size) break;

            if (cell_sz > 0) {
                // Free cell — check for deleted structures
                ++report.free_cells_scanned;
                report.total_free_bytes += abs_sz;

                uint32_t cell_offset = static_cast<uint32_t>(cell_pos - HIVE_DATA_START);

                if (abs_sz >= sizeof(NkCell) + 1 && buf[cell_pos+4] == 'n' && buf[cell_pos+5] == 'k') {
                    try_recover_nk(cell_offset, hbin_offset, report);
                } else if (abs_sz >= sizeof(VkCell) && buf[cell_pos+4] == 'v' && buf[cell_pos+5] == 'k') {
                    try_recover_vk(cell_offset, hbin_offset, report);
                }
            }

            cell_pos += abs_sz;
        }

        hbin_pos += hbin_size;
    }

    report.hbins_scanned = hbin_count;
    return report;
}

} // namespace regbroker::core
