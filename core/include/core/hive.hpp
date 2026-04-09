#pragma once
#include "structures.hpp"
#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace regbroker::core {

// Callback for traversal: return false to stop
using TraversalCallback = std::function<bool(const RegKey&, int depth)>;

class Hive {
public:
    // Load a hive file from disk into memory
    static std::unique_ptr<Hive> open(const std::string& path);

    // Hive metadata
    const HiveInfo& info() const { return info_; }

    // Navigation — returns nullopt if path doesn't exist
    std::optional<RegKey>              root() const;
    std::optional<RegKey>              get_key(const std::string& path) const;
    std::optional<RegKey>              get_key_by_offset(uint32_t cell_offset) const;
    std::vector<RegKey>                list_subkeys(const RegKey& key) const;
    std::vector<RegValue>              list_values(const RegKey& key) const;
    std::optional<RegValue>            get_value(const RegKey& key, const std::string& name) const;

    // Deep traversal
    void traverse(const RegKey& start, TraversalCallback cb, int max_depth = -1) const;

    // Raw data access for a value
    std::vector<uint8_t> read_value_data(const VkCell* vk, uint32_t file_offset) const;

    // Raw buffer access (for recovery engine)
    const uint8_t*  data()      const { return buf_.data(); }
    size_t          data_size() const { return buf_.size(); }

    // Convert a cell offset to a raw pointer into the buffer
    // Returns nullptr if offset is invalid
    template<typename T>
    const T* cell_ptr(uint32_t cell_offset) const {
        if (cell_offset == INVALID_OFFSET) return nullptr;
        uint64_t file_off = static_cast<uint64_t>(HIVE_DATA_START) + cell_offset;
        if (file_off + sizeof(T) > buf_.size()) return nullptr;
        return reinterpret_cast<const T*>(buf_.data() + file_off);
    }

    bool is_allocated_cell(uint32_t cell_offset) const;

private:
    Hive() = default;

    bool load(const std::string& path);
    bool validate_header() const;

    // Internal helpers
    std::string         read_key_name(const NkCell* nk) const;
    std::string         read_value_name(const VkCell* vk) const;
    std::vector<uint32_t> resolve_subkey_list(uint32_t list_offset) const;
    std::vector<uint32_t> resolve_value_list(uint32_t list_offset, uint32_t count) const;

    RegKey  make_reg_key(const NkCell* nk, uint32_t cell_offset,
                         const std::string& path) const;
    RegValue make_reg_value(const VkCell* vk, uint32_t cell_offset) const;

    std::vector<uint8_t> buf_;
    HiveInfo             info_;
    uint32_t             root_cell_offset_ = INVALID_OFFSET;
};

} // namespace regbroker::core
