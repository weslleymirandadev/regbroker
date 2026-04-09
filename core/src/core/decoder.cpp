#include "../../include/core/decoder.hpp"
#include <algorithm>
#include <cstring>
#include <iomanip>
#include <sstream>

namespace regbroker::core {

// ── UTF-16LE → UTF-8 ──────────────────────────────────────────────────────────

static std::string utf16le_to_utf8(const uint8_t* data, size_t byte_len) {
    std::string out;
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

// ── Type name ─────────────────────────────────────────────────────────────────

std::string Decoder::type_name(ValueType t) {
    switch (t) {
        case ValueType::None:                 return "REG_NONE";
        case ValueType::Sz:                   return "REG_SZ";
        case ValueType::ExpandSz:             return "REG_EXPAND_SZ";
        case ValueType::Binary:               return "REG_BINARY";
        case ValueType::Dword:                return "REG_DWORD";
        case ValueType::DwordBE:              return "REG_DWORD_BIG_ENDIAN";
        case ValueType::Link:                 return "REG_LINK";
        case ValueType::MultiSz:              return "REG_MULTI_SZ";
        case ValueType::ResourceList:         return "REG_RESOURCE_LIST";
        case ValueType::FullDescriptor:       return "REG_FULL_RESOURCE_DESCRIPTOR";
        case ValueType::ResourceRequirements: return "REG_RESOURCE_REQUIREMENTS_LIST";
        case ValueType::Qword:                return "REG_QWORD";
        default: {
            char buf[32];
            snprintf(buf, sizeof(buf), "REG_UNKNOWN(0x%x)", static_cast<uint32_t>(t));
            return buf;
        }
    }
}

// ── String decoders ───────────────────────────────────────────────────────────

std::string Decoder::decode_sz(const std::vector<uint8_t>& data) {
    if (data.empty()) return "";
    // Detect UTF-16LE by checking for null bytes at even positions
    bool is_utf16 = data.size() >= 2 && data[1] == 0;
    if (is_utf16) {
        return utf16le_to_utf8(data.data(), data.size());
    }
    // ASCII / UTF-8
    const char* s = reinterpret_cast<const char*>(data.data());
    size_t len = strnlen(s, data.size());
    return std::string(s, len);
}

std::string Decoder::decode_multi_sz(const std::vector<uint8_t>& data) {
    if (data.empty()) return "";
    std::string result;
    bool is_utf16 = data.size() >= 2 && data[1] == 0;

    if (is_utf16) {
        size_t i = 0;
        while (i + 1 < data.size()) {
            uint16_t wc = static_cast<uint16_t>(data[i]) | (static_cast<uint16_t>(data[i+1]) << 8);
            if (wc == 0) {
                // Double null = end of list
                if (!result.empty() && result.back() != '\n') result += '\n';
                i += 2;
                if (i + 1 < data.size()) {
                    uint16_t next = static_cast<uint16_t>(data[i]) | (static_cast<uint16_t>(data[i+1]) << 8);
                    if (next == 0) break;
                }
            } else {
                // Encode wc to UTF-8
                if (wc < 0x80) {
                    result += static_cast<char>(wc);
                } else if (wc < 0x800) {
                    result += static_cast<char>(0xC0 | (wc >> 6));
                    result += static_cast<char>(0x80 | (wc & 0x3F));
                } else {
                    result += static_cast<char>(0xE0 | (wc >> 12));
                    result += static_cast<char>(0x80 | ((wc >> 6) & 0x3F));
                    result += static_cast<char>(0x80 | (wc & 0x3F));
                }
                i += 2;
            }
        }
    } else {
        const char* p = reinterpret_cast<const char*>(data.data());
        size_t rem = data.size();
        while (rem > 0 && *p) {
            size_t len = strnlen(p, rem);
            result += std::string(p, len) + "\n";
            p   += len + 1;
            rem -= len + 1;
        }
    }
    return result;
}

// ── FILETIME conversions ──────────────────────────────────────────────────────

// Windows FILETIME = 100-nanosecond intervals since 1601-01-01 UTC
static const uint64_t FILETIME_EPOCH_DIFF = 116444736000000000ULL; // to Unix epoch

int64_t Decoder::filetime_to_unix(uint64_t ft) {
    if (ft < FILETIME_EPOCH_DIFF) return 0;
    return static_cast<int64_t>((ft - FILETIME_EPOCH_DIFF) / 10000000ULL);
}

std::string Decoder::filetime_to_string(uint64_t ft) {
    if (ft == 0) return "1601-01-01T00:00:00Z";
    int64_t unix_ts = filetime_to_unix(ft);
    if (unix_ts <= 0) return "1601-01-01T00:00:00Z";

    time_t t = static_cast<time_t>(unix_ts);
    struct tm tm_val;
#ifdef _WIN32
    gmtime_s(&tm_val, &t);
#else
    gmtime_r(&t, &tm_val);
#endif
    char buf[32];
    snprintf(buf, sizeof(buf), "%04d-%02d-%02dT%02d:%02d:%02dZ",
             tm_val.tm_year + 1900, tm_val.tm_mon + 1, tm_val.tm_mday,
             tm_val.tm_hour, tm_val.tm_min, tm_val.tm_sec);
    return buf;
}

// ── Main to_string ────────────────────────────────────────────────────────────

std::string Decoder::to_string(const RegValue& value) {
    const auto& d = value.data;

    switch (value.type) {
        case ValueType::Sz:
        case ValueType::ExpandSz:
        case ValueType::Link:
            return decode_sz(d);

        case ValueType::MultiSz:
            return decode_multi_sz(d);

        case ValueType::Dword:
            if (d.size() >= 4) {
                uint32_t v;
                std::memcpy(&v, d.data(), 4);
                char buf[32];
                snprintf(buf, sizeof(buf), "0x%08x (%u)", v, v);
                return buf;
            }
            break;

        case ValueType::DwordBE:
            if (d.size() >= 4) {
                uint32_t v = (static_cast<uint32_t>(d[0]) << 24) |
                             (static_cast<uint32_t>(d[1]) << 16) |
                             (static_cast<uint32_t>(d[2]) << 8)  |
                              static_cast<uint32_t>(d[3]);
                char buf[32];
                snprintf(buf, sizeof(buf), "0x%08x (%u)", v, v);
                return buf;
            }
            break;

        case ValueType::Qword:
            if (d.size() >= 8) {
                uint64_t v;
                std::memcpy(&v, d.data(), 8);
                char buf[48];
                snprintf(buf, sizeof(buf), "0x%016llx (%llu)",
                         static_cast<unsigned long long>(v),
                         static_cast<unsigned long long>(v));
                return buf;
            }
            break;

        case ValueType::None:
            if (d.empty()) return "(empty)";
            break;

        default:
            break;
    }

    // Fallback: hex string
    if (d.empty()) return "(empty)";
    size_t show = std::min(d.size(), static_cast<size_t>(64));
    std::ostringstream oss;
    for (size_t i = 0; i < show; ++i) {
        if (i) oss << " ";
        oss << std::hex << std::setw(2) << std::setfill('0')
            << static_cast<int>(d[i]);
    }
    if (show < d.size()) oss << " ...(" << d.size() << " bytes)";
    return oss.str();
}

// ── Hex dump ──────────────────────────────────────────────────────────────────

std::string Decoder::hex_dump(const std::vector<uint8_t>& data, size_t max_bytes) {
    if (data.empty()) return "(empty)";
    size_t n = std::min(data.size(), max_bytes);
    std::ostringstream oss;

    for (size_t row = 0; row < n; row += 16) {
        char addr_buf[12];
        snprintf(addr_buf, sizeof(addr_buf), "%08zx  ", row);
        oss << addr_buf;

        // Hex bytes
        for (size_t col = 0; col < 16; ++col) {
            if (row + col < n) {
                char byte_buf[4];
                snprintf(byte_buf, sizeof(byte_buf), "%02x ", data[row + col]);
                oss << byte_buf;
            } else {
                oss << "   ";
            }
            if (col == 7) oss << " ";
        }
        oss << " |";
        // ASCII sidebar
        for (size_t col = 0; col < 16 && row + col < n; ++col) {
            uint8_t c = data[row + col];
            oss << (char)(c >= 0x20 && c < 0x7F ? c : '.');
        }
        oss << "|\n";
    }
    if (n < data.size()) {
        oss << "  ... (" << (data.size() - n) << " more bytes)\n";
    }
    return oss.str();
}

} // namespace regbroker::core
