#pragma once
#include "structures.hpp"
#include <vector>

namespace regbroker::core {

class Hive;

struct RecoveredKey {
    RegKey   key;
    uint32_t cell_offset;
    uint32_t hbin_offset;   // which HBIN block
    bool     parent_reachable;
    std::string reason;     // why we think this is recoverable
};

struct RecoveredValue {
    RegValue value;
    uint32_t cell_offset;
    uint32_t hbin_offset;
    bool     data_intact;
    std::string reason;
};

struct RecoveryReport {
    std::vector<RecoveredKey>   keys;
    std::vector<RecoveredValue> values;
    size_t  hbins_scanned  = 0;
    size_t  free_cells_scanned = 0;
    size_t  total_free_bytes   = 0;
};

class RecoveryEngine {
public:
    explicit RecoveryEngine(const Hive& hive);

    // Full scan of all HBIN free cells for deleted NK/VK cells
    RecoveryReport scan();

    // Quick scan limited to the top N HBIN blocks
    RecoveryReport scan_partial(size_t max_hbins);

private:
    bool try_recover_nk(uint32_t cell_offset, uint32_t hbin_offset, RecoveryReport& report);
    bool try_recover_vk(uint32_t cell_offset, uint32_t hbin_offset, RecoveryReport& report);
    bool is_valid_nk_candidate(const NkCell* nk, uint32_t cell_size) const;
    bool is_valid_vk_candidate(const VkCell* vk, uint32_t cell_size) const;
    bool is_offset_reachable(uint32_t offset) const;

    const Hive& hive_;
};

} // namespace regbroker::core
