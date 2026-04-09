#include "../../include/core/hive.hpp"
#include <algorithm>
#include <cassert>
#include <cstring>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>

namespace regbroker::core {

// ── Helpers ───────────────────────────────────────────────────────────────────

static std::string utf16le_to_utf8(const uint8_t* data, size_t byte_len) {
    std::string out;
    out.reserve(byte_len / 2);
    for (size_t i = 0; i + 1 < byte_len; i += 2) {
        uint16_t wc = static_cast<uint16_t>(data[i]) | (static_cast<uint16_t>(data[i+1]) << 8);
        if (wc == 0) break;
        if (wc < 0x80) {
            out += static_cast<char>(wc);
        } else if (wc < 0x800) {
            out += static_cast<char>(0xC0 | (wc >> 6));
            out += static_cast<char>(0x80 | (wc & 0x3F));
        } else {
            out += static_cast<char>(0xE0 | (wc >> 12));
            out += static_cast<char>(0x80 | ((wc >> 6) & 0x3F));
            out += static_cast<char>(0x80 | (wc & 0x3F));
        }
    }
    return out;
}

// ── Hive::open ────────────────────────────────────────────────────────────────

std::unique_ptr<Hive> Hive::open(const std::string& path) {
    auto hive = std::unique_ptr<Hive>(new Hive());
    if (!hive->load(path)) return nullptr;
    return hive;
}

bool Hive::load(const std::string& path) {
    std::ifstream f(path, std::ios::binary | std::ios::ate);
    if (!f) {
        std::cerr << "Cannot open: " << path << "\n";
        return false;
    }
    auto size = static_cast<size_t>(f.tellg());
    if (size < sizeof(RegfHeader) + sizeof(HbinHeader)) {
        std::cerr << "File too small to be a hive\n";
        return false;
    }
    f.seekg(0);
    buf_.resize(size);
    f.read(reinterpret_cast<char*>(buf_.data()), size);
    if (!f) {
        std::cerr << "Read error\n";
        return false;
    }

    if (!validate_header()) return false;

    const auto* hdr = reinterpret_cast<const RegfHeader*>(buf_.data());
    info_.major_version  = hdr->major_version;
    info_.minor_version  = hdr->minor_version;
    info_.timestamp      = hdr->timestamp;
    info_.hive_data_size = hdr->hive_data_size;
    info_.filename       = path;

    root_cell_offset_ = hdr->root_cell_offset;

    // Extract the hive filename from the embedded unicode path
    std::string embedded = utf16le_to_utf8(
        reinterpret_cast<const uint8_t*>(hdr->filename), 64);
    if (!embedded.empty()) {
        auto pos = embedded.rfind('\\');
        info_.filename = (pos != std::string::npos)
            ? embedded.substr(pos + 1) : embedded;
    }

    // Read root key name
    const auto* root_nk = cell_ptr<NkCell>(root_cell_offset_);
    if (root_nk && root_nk->signature[0] == 'n' && root_nk->signature[1] == 'k') {
        info_.root_key_name = read_key_name(root_nk);
    }

    return true;
}

bool Hive::validate_header() const {
    if (buf_.size() < sizeof(RegfHeader)) return false;
    const auto* hdr = reinterpret_cast<const RegfHeader*>(buf_.data());
    if (std::memcmp(hdr->signature, "regf", 4) != 0) {
        std::cerr << "Invalid REGF signature\n";
        return false;
    }
    // Check first HBIN
    if (buf_.size() >= HIVE_DATA_START + sizeof(HbinHeader)) {
        const auto* hbin = reinterpret_cast<const HbinHeader*>(buf_.data() + HIVE_DATA_START);
        if (std::memcmp(hbin->signature, "hbin", 4) != 0) {
            std::cerr << "Invalid HBIN signature at offset 0x1000\n";
            return false;
        }
    }
    return true;
}

// ── Cell helpers ──────────────────────────────────────────────────────────────

bool Hive::is_allocated_cell(uint32_t cell_offset) const {
    if (cell_offset == INVALID_OFFSET) return false;
    uint64_t file_off = HIVE_DATA_START + cell_offset;
    if (file_off + 4 > buf_.size()) return false;
    int32_t sz;
    std::memcpy(&sz, buf_.data() + file_off, 4);
    return sz < 0;  // negative = allocated
}

// ── Name reading ──────────────────────────────────────────────────────────────

std::string Hive::read_key_name(const NkCell* nk) const {
    const uint8_t* name_ptr = reinterpret_cast<const uint8_t*>(nk) + sizeof(NkCell);
    if (nk->flags & NK_ASCII_NAME) {
        return std::string(reinterpret_cast<const char*>(name_ptr), nk->key_name_len);
    } else {
        return utf16le_to_utf8(name_ptr, nk->key_name_len);
    }
}

std::string Hive::read_value_name(const VkCell* vk) const {
    if (vk->value_name_len == 0) return "";  // default value
    const uint8_t* name_ptr = reinterpret_cast<const uint8_t*>(vk) + sizeof(VkCell);
    if (vk->flags & VK_ASCII_NAME) {
        return std::string(reinterpret_cast<const char*>(name_ptr), vk->value_name_len);
    } else {
        return utf16le_to_utf8(name_ptr, vk->value_name_len);
    }
}

// ── Root ─────────────────────────────────────────────────────────────────────

std::optional<RegKey> Hive::root() const {
    const auto* nk = cell_ptr<NkCell>(root_cell_offset_);
    if (!nk || nk->signature[0] != 'n' || nk->signature[1] != 'k') return std::nullopt;
    auto key = make_reg_key(nk, root_cell_offset_, "\\");
    key.is_root = true;
    return key;
}

// ── Navigation ────────────────────────────────────────────────────────────────

std::optional<RegKey> Hive::get_key(const std::string& path) const {
    auto r = root();
    if (!r) return std::nullopt;
    if (path == "\\" || path.empty()) return r;

    // Strip leading backslash
    std::string p = path;
    if (!p.empty() && p[0] == '\\') p = p.substr(1);

    RegKey current = *r;
    std::string current_path = "\\";

    while (!p.empty()) {
        auto sep = p.find('\\');
        std::string component = (sep == std::string::npos) ? p : p.substr(0, sep);
        p = (sep == std::string::npos) ? "" : p.substr(sep + 1);

        bool found = false;
        for (auto& sub : list_subkeys(current)) {
            if (_stricmp(sub.name.c_str(), component.c_str()) == 0) {
                current = sub;
                current_path += (current_path.back() == '\\' ? "" : std::string("\\")) + component;
                found = true;
                break;
            }
        }
        if (!found) return std::nullopt;
    }
    return current;
}

std::optional<RegKey> Hive::get_key_by_offset(uint32_t cell_offset) const {
    const auto* nk = cell_ptr<NkCell>(cell_offset);
    if (!nk || nk->signature[0] != 'n' || nk->signature[1] != 'k') return std::nullopt;
    return make_reg_key(nk, cell_offset, "?");
}

// ── Subkey list resolution ────────────────────────────────────────────────────

std::vector<uint32_t> Hive::resolve_subkey_list(uint32_t list_offset) const {
    std::vector<uint32_t> offsets;
    if (list_offset == INVALID_OFFSET || list_offset == 0) return offsets;

    const uint8_t* p = reinterpret_cast<const uint8_t*>(cell_ptr<CellHeader>(list_offset));
    if (!p) return offsets;

    char sig[2];
    std::memcpy(sig, p + 4, 2);

    if (sig[0] == 'r' && sig[1] == 'i') {
        // RI: root index — list of sublists
        const auto* ri = reinterpret_cast<const RiHeader*>(p);
        const auto* elements = reinterpret_cast<const RiElement*>(p + sizeof(RiHeader));
        for (uint16_t i = 0; i < ri->num_elements; ++i) {
            auto sub = resolve_subkey_list(elements[i].list_offset);
            offsets.insert(offsets.end(), sub.begin(), sub.end());
        }
    } else if (sig[0] == 'l' && sig[1] == 'f') {
        const auto* lf = reinterpret_cast<const LfHeader*>(p);
        const auto* elements = reinterpret_cast<const LfElement*>(p + sizeof(LfHeader));
        for (uint16_t i = 0; i < lf->num_elements; ++i) {
            offsets.push_back(elements[i].key_offset);
        }
    } else if (sig[0] == 'l' && sig[1] == 'h') {
        const auto* lh = reinterpret_cast<const LhHeader*>(p);
        const auto* elements = reinterpret_cast<const LhElement*>(p + sizeof(LhHeader));
        for (uint16_t i = 0; i < lh->num_elements; ++i) {
            offsets.push_back(elements[i].key_offset);
        }
    } else if (sig[0] == 'l' && sig[1] == 'i') {
        const auto* li = reinterpret_cast<const LiHeader*>(p);
        const auto* elements = reinterpret_cast<const LiElement*>(p + sizeof(LiHeader));
        for (uint16_t i = 0; i < li->num_elements; ++i) {
            offsets.push_back(elements[i].key_offset);
        }
    }
    return offsets;
}

std::vector<uint32_t> Hive::resolve_value_list(uint32_t list_offset, uint32_t count) const {
    std::vector<uint32_t> offsets;
    if (list_offset == INVALID_OFFSET || list_offset == 0 || count == 0) return offsets;

    uint64_t file_off = HIVE_DATA_START + list_offset;
    if (file_off + 4 > buf_.size()) return offsets;

    // Value list cell: 4-byte size header, then array of uint32_t offsets
    // (the cell_size header comes first, so data starts at file_off + 4)
    const uint32_t* list = reinterpret_cast<const uint32_t*>(buf_.data() + file_off + 4);
    size_t available = (buf_.size() - file_off - 4) / sizeof(uint32_t);
    size_t n = std::min(static_cast<size_t>(count), available);

    for (size_t i = 0; i < n; ++i) {
        offsets.push_back(list[i]);
    }
    return offsets;
}

// ── List subkeys / values ─────────────────────────────────────────────────────

std::vector<RegKey> Hive::list_subkeys(const RegKey& key) const {
    std::vector<RegKey> result;
    const auto* nk = cell_ptr<NkCell>(key.cell_offset);
    if (!nk) return result;

    auto offsets = resolve_subkey_list(nk->subkeys_list_offset);
    result.reserve(offsets.size());

    for (auto off : offsets) {
        const auto* sub_nk = cell_ptr<NkCell>(off);
        if (!sub_nk || sub_nk->signature[0] != 'n' || sub_nk->signature[1] != 'k') continue;
        std::string child_path = key.path;
        if (child_path.back() != '\\') child_path += '\\';
        std::string name = read_key_name(sub_nk);
        child_path += name;
        result.push_back(make_reg_key(sub_nk, off, child_path));
    }
    return result;
}

std::vector<RegValue> Hive::list_values(const RegKey& key) const {
    std::vector<RegValue> result;
    const auto* nk = cell_ptr<NkCell>(key.cell_offset);
    if (!nk || nk->num_values == 0) return result;

    auto offsets = resolve_value_list(nk->values_list_offset, nk->num_values);
    result.reserve(offsets.size());

    for (auto off : offsets) {
        const auto* vk = cell_ptr<VkCell>(off);
        if (!vk || vk->signature[0] != 'v' || vk->signature[1] != 'k') continue;
        result.push_back(make_reg_value(vk, off));
    }
    return result;
}

std::optional<RegValue> Hive::get_value(const RegKey& key, const std::string& name) const {
    for (auto& v : list_values(key)) {
        if (_stricmp(v.name.c_str(), name.c_str()) == 0) return v;
    }
    return std::nullopt;
}

// ── Value data reading ────────────────────────────────────────────────────────

std::vector<uint8_t> Hive::read_value_data(const VkCell* vk, uint32_t /*file_offset*/) const {
    std::vector<uint8_t> data;
    if (!vk) return data;

    uint32_t raw_size = vk->data_size;
    bool     inline_data = (raw_size & 0x80000000) != 0;
    uint32_t data_size   = raw_size & 0x7FFFFFFF;

    if (data_size == 0) return data;

    if (inline_data) {
        // Data is stored inline in the data_offset field (up to 4 bytes)
        size_t n = std::min(data_size, static_cast<uint32_t>(4));
        const uint8_t* ptr = reinterpret_cast<const uint8_t*>(&vk->data_offset);
        data.assign(ptr, ptr + n);
        return data;
    }

    uint32_t data_offset = vk->data_offset;
    if (data_offset == INVALID_OFFSET) return data;

    // Check for DB cell (large data, > 16344 bytes)
    uint64_t file_off = HIVE_DATA_START + data_offset;
    if (file_off + 6 <= buf_.size()) {
        char sig[2];
        std::memcpy(sig, buf_.data() + file_off + 4, 2);
        if (sig[0] == 'd' && sig[1] == 'b') {
            const auto* db = reinterpret_cast<const DbCell*>(buf_.data() + file_off);
            // Follow segment list
            uint64_t seg_list_off = HIVE_DATA_START + db->segment_list_offset;
            if (seg_list_off + 4 + db->num_segments * 4 <= buf_.size()) {
                const uint32_t* segments = reinterpret_cast<const uint32_t*>(
                    buf_.data() + seg_list_off + 4);
                for (uint16_t i = 0; i < db->num_segments && data.size() < data_size; ++i) {
                    uint64_t seg_off = HIVE_DATA_START + segments[i];
                    if (seg_off + 4 >= buf_.size()) break;
                    int32_t seg_cell_size;
                    std::memcpy(&seg_cell_size, buf_.data() + seg_off, 4);
                    size_t seg_data_size = static_cast<size_t>(std::abs(seg_cell_size)) - 4;
                    size_t remaining = data_size - data.size();
                    size_t to_copy = std::min(seg_data_size, remaining);
                    if (seg_off + 4 + to_copy > buf_.size()) break;
                    const uint8_t* seg_data = buf_.data() + seg_off + 4;
                    data.insert(data.end(), seg_data, seg_data + to_copy);
                }
            }
            return data;
        }
    }

    // Normal data cell: skip the 4-byte cell size header
    if (file_off + 4 + data_size > buf_.size()) {
        data_size = static_cast<uint32_t>(buf_.size() - file_off - 4);
    }
    if (data_size > 0) {
        const uint8_t* ptr = buf_.data() + file_off + 4;
        data.assign(ptr, ptr + data_size);
    }
    return data;
}

// ── Factory helpers ───────────────────────────────────────────────────────────

RegKey Hive::make_reg_key(const NkCell* nk, uint32_t cell_offset,
                           const std::string& path) const {
    RegKey key;
    key.name        = read_key_name(nk);
    key.path        = path;
    key.timestamp   = nk->timestamp;
    key.cell_offset = cell_offset;
    key.file_offset = HIVE_DATA_START + cell_offset;
    key.flags       = nk->flags;
    key.num_values  = nk->num_values;
    key.num_subkeys = nk->num_subkeys;
    return key;
}

RegValue Hive::make_reg_value(const VkCell* vk, uint32_t cell_offset) const {
    RegValue val;
    val.name        = read_value_name(vk);
    val.type        = static_cast<ValueType>(vk->data_type);
    val.cell_offset = cell_offset;
    val.data        = read_value_data(vk, HIVE_DATA_START + cell_offset);
    return val;
}

// ── Traversal ─────────────────────────────────────────────────────────────────

void Hive::traverse(const RegKey& start, TraversalCallback cb, int max_depth) const {
    if (max_depth == 0) return;

    struct Frame { RegKey key; int depth; };
    std::vector<Frame> stack;
    stack.push_back({start, 0});

    while (!stack.empty()) {
        auto [key, depth] = stack.back();
        stack.pop_back();

        if (!cb(key, depth)) return;

        if (max_depth < 0 || depth + 1 <= max_depth) {
            auto subs = list_subkeys(key);
            // Push in reverse so we process in alphabetical order
            for (auto it = subs.rbegin(); it != subs.rend(); ++it) {
                stack.push_back({*it, depth + 1});
            }
        }
    }
}

} // namespace regbroker::core
