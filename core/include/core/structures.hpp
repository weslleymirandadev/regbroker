#pragma once
#include <cstdint>
#include <string>
#include <vector>

namespace regbroker::core {

// ── Registry value types ─────────────────────────────────────────────────────
enum class ValueType : uint32_t {
    None                = 0,
    Sz                  = 1,   // REG_SZ
    ExpandSz            = 2,   // REG_EXPAND_SZ
    Binary              = 3,   // REG_BINARY
    Dword               = 4,   // REG_DWORD (little-endian)
    DwordBE             = 5,   // REG_DWORD_BIG_ENDIAN
    Link                = 6,   // REG_LINK
    MultiSz             = 7,   // REG_MULTI_SZ
    ResourceList        = 8,
    FullDescriptor      = 9,
    ResourceRequirements = 10,
    Qword               = 11,  // REG_QWORD
    Unknown             = 0xFFFFFFFF,
};

// NK flags
enum NKFlags : uint16_t {
    NK_VOLATILE    = 0x0001,
    NK_HIVE_EXIT   = 0x0002,
    NK_HIVE_ENTRY  = 0x0004,  // root key
    NK_NO_DELETE   = 0x0008,
    NK_SYM_LINK    = 0x0010,
    NK_ASCII_NAME  = 0x0020,  // name is ASCII (else UTF-16LE)
    NK_PREDEFINED  = 0x0040,
    NK_VIRT_MIRROR = 0x0080,
    NK_VIRT_TARGET = 0x0100,
    NK_VIRT_STORE  = 0x0200,
};

// VK flags
enum VKFlags : uint16_t {
    VK_ASCII_NAME  = 0x0001,
};

// Special offsets
constexpr uint32_t INVALID_OFFSET  = 0xFFFFFFFF;
constexpr uint32_t HIVE_DATA_START = 0x1000;    // REGF header is 512 bytes, padded to 4096

// ── On-disk packed structures ────────────────────────────────────────────────
#pragma pack(push, 1)

// REGF header — first 512 bytes of a hive file
struct RegfHeader {
    char     signature[4];      // "regf"
    uint32_t seqnum1;
    uint32_t seqnum2;
    uint64_t timestamp;         // FILETIME of last write
    uint32_t major_version;     // currently 1
    uint32_t minor_version;     // 2–6 depending on Windows version
    uint32_t type;              // 0 = primary, 1 = log
    uint32_t format;            // 1 = direct memory load
    uint32_t root_cell_offset;  // offset from start of HBIN data
    uint32_t hive_data_size;    // total size of all HBIN data
    uint32_t clustering_factor;
    char     filename[64];      // unicode basename of the hive
    uint8_t  guid[16];
    uint8_t  reserved[340];
    uint32_t checksum;          // XOR of first 127 dwords
};
static_assert(sizeof(RegfHeader) == 512, "RegfHeader must be 512 bytes");

// HBIN block header — 32 bytes, followed by cells
struct HbinHeader {
    char     signature[4];  // "hbin"
    uint32_t offset;        // this block's offset from start of HBIN data
    uint32_t size;          // size of this block (multiple of 4096)
    uint8_t  unknown[8];
    uint64_t timestamp;     // FILETIME (meaningful only in the first HBIN)
    uint32_t spare;
};
static_assert(sizeof(HbinHeader) == 32, "HbinHeader must be 32 bytes");

// Cell header — all cells start with a signed 32-bit size
//   negative = allocated  |size| = cell size
//   positive = free        size  = cell size
struct CellHeader {
    int32_t size;
};

// NK cell — Named Key (registry key node)
struct NkCell {
    int32_t  cell_size;
    char     signature[2];                  // "nk"
    uint16_t flags;
    uint64_t timestamp;                     // FILETIME
    uint32_t access_bits;
    uint32_t parent_offset;
    uint32_t num_subkeys;
    uint32_t num_volatile_subkeys;
    uint32_t subkeys_list_offset;
    uint32_t volatile_subkeys_list_offset;
    uint32_t num_values;
    uint32_t values_list_offset;
    uint32_t security_key_offset;
    uint32_t class_name_offset;
    uint16_t max_subkey_name_len;
    uint16_t max_subkey_class_name_len;
    uint32_t max_value_name_len;
    uint32_t max_value_data_len;
    uint32_t work_var;
    uint16_t key_name_len;
    uint16_t class_name_len;
    // key_name follows (ASCII or UTF-16LE based on NK_ASCII_NAME flag)
};

// VK cell — Value Key (registry value)
struct VkCell {
    int32_t  cell_size;
    char     signature[2];      // "vk"
    uint16_t value_name_len;    // 0 = default value
    uint32_t data_size;         // bit 31 set = data is inline in data_offset
    uint32_t data_offset;       // or inline data if bit 31 of data_size is set
    uint32_t data_type;
    uint16_t flags;             // bit 0: name is ASCII
    uint16_t spare;
    // value_name follows
};

// SK cell — Security Key
struct SkCell {
    int32_t  cell_size;
    char     signature[2];      // "sk"
    uint16_t spare;
    uint32_t prev_sk_offset;
    uint32_t next_sk_offset;
    uint32_t reference_count;
    uint32_t security_data_size;
    // SECURITY_DESCRIPTOR follows
};

// LF cell — Leaf with fast name comparison (first 4 chars as hint)
struct LfHeader {
    int32_t  cell_size;
    char     signature[2];      // "lf"
    uint16_t num_elements;
};
struct LfElement {
    uint32_t key_offset;
    char     name_hint[4];
};

// LH cell — Leaf with name hash
struct LhHeader {
    int32_t  cell_size;
    char     signature[2];      // "lh"
    uint16_t num_elements;
};
struct LhElement {
    uint32_t key_offset;
    uint32_t name_hash;
};

// RI cell — Root Index (list of sublist offsets, for large key counts)
struct RiHeader {
    int32_t  cell_size;
    char     signature[2];      // "ri"
    uint16_t num_elements;
};
struct RiElement {
    uint32_t list_offset;
};

// LI cell — Leaf Index (direct offsets, older format)
struct LiHeader {
    int32_t  cell_size;
    char     signature[2];      // "li"
    uint16_t num_elements;
};
struct LiElement {
    uint32_t key_offset;
};

// DB cell — Data Block (large value segmentation)
struct DbCell {
    int32_t  cell_size;
    char     signature[2];      // "db"
    uint16_t num_segments;
    uint32_t segment_list_offset;
};

#pragma pack(pop)

// ── Decoded high-level types ─────────────────────────────────────────────────

struct RegKey {
    std::string name;
    std::string path;
    uint64_t    timestamp   = 0;
    uint32_t    file_offset = 0;    // absolute offset in hive file
    uint32_t    cell_offset = 0;    // cell offset (relative to HBIN data)
    uint16_t    flags       = 0;
    uint32_t    num_values  = 0;
    uint32_t    num_subkeys = 0;
    bool        deleted     = false;
    bool        is_root     = false;
};

struct RegValue {
    std::string          name;      // empty string = default value
    ValueType            type      = ValueType::None;
    std::vector<uint8_t> data;
    uint32_t             cell_offset = 0;
    bool                 deleted    = false;
};

struct HiveInfo {
    std::string filename;
    uint32_t    major_version = 0;
    uint32_t    minor_version = 0;
    uint64_t    timestamp     = 0;
    uint32_t    hive_data_size = 0;
    std::string root_key_name;
};

} // namespace regbroker::core
