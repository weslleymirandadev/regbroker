#pragma once
#include "structures.hpp"
#include <string>

namespace regbroker::core {

class Decoder {
public:
    // Human-readable string representation of a registry value
    static std::string to_string(const RegValue& value);

    // Hex dump, 16 bytes per line with ASCII sidebar
    static std::string hex_dump(const std::vector<uint8_t>& data, size_t max_bytes = 512);

    // Type name string
    static std::string type_name(ValueType t);

    // Decode REG_SZ / REG_EXPAND_SZ / REG_LINK (UTF-16LE → UTF-8)
    static std::string decode_sz(const std::vector<uint8_t>& data);

    // Decode REG_MULTI_SZ → newline-separated strings
    static std::string decode_multi_sz(const std::vector<uint8_t>& data);

    // FILETIME → ISO-8601 string
    static std::string filetime_to_string(uint64_t ft);

    // FILETIME → Unix timestamp
    static int64_t filetime_to_unix(uint64_t ft);
};

} // namespace regbroker::core
