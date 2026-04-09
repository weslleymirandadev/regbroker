#include "../include/core/hive.hpp"
#include "../include/core/decoder.hpp"
#include "../include/core/recovery.hpp"
#include <iostream>
#include <sstream>
#include <string>
#include <vector>
#include <cstring>

// ── Minimal JSON serialization helpers ───────────────────────────────────────

static std::string json_escape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 4);
    for (unsigned char c : s) {
        switch (c) {
            case '"':  out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n";  break;
            case '\r': out += "\\t";  break;
            case '\t': out += "\\t";  break;
            default:
                if (c < 0x20) {
                    char buf[8];
                    snprintf(buf, sizeof(buf), "\\u%04x", c);
                    out += buf;
                } else {
                    out += c;
                }
        }
    }
    return out;
}

static std::string jstr(const std::string& s) {
    return "\"" + json_escape(s) + "\"";
}

static std::string jkey_to_obj(const regbroker::core::RegKey& k) {
    using namespace regbroker::core;
    std::ostringstream o;
    o << "{"
      << "\"name\":"        << jstr(k.name)        << ","
      << "\"path\":"        << jstr(k.path)        << ","
      << "\"timestamp\":"   << jstr(Decoder::filetime_to_string(k.timestamp)) << ","
      << "\"timestamp_unix\":" << Decoder::filetime_to_unix(k.timestamp) << ","
      << "\"cell_offset\":" << k.cell_offset       << ","
      << "\"num_subkeys\":" << k.num_subkeys        << ","
      << "\"num_values\":"  << k.num_values         << ","
      << "\"is_root\":"     << (k.is_root ? "true" : "false") << ","
      << "\"deleted\":"     << (k.deleted ? "true" : "false")
      << "}";
    return o.str();
}

static std::string jvalue_to_obj(const regbroker::core::RegValue& v) {
    using namespace regbroker::core;
    std::string decoded = Decoder::to_string(v);
    std::string hex;
    if (v.data.size() <= 256) {
        for (uint8_t b : v.data) {
            char buf[4];
            snprintf(buf, sizeof(buf), "%02x", b);
            hex += buf;
        }
    }
    std::ostringstream o;
    o << "{"
      << "\"name\":"        << jstr(v.name.empty() ? "(Default)" : v.name) << ","
      << "\"type\":"        << jstr(Decoder::type_name(v.type)) << ","
      << "\"type_id\":"     << static_cast<uint32_t>(v.type) << ","
      << "\"size\":"        << v.data.size() << ","
      << "\"value\":"       << jstr(decoded) << ","
      << "\"hex\":"         << jstr(hex) << ","
      << "\"cell_offset\":" << v.cell_offset << ","
      << "\"deleted\":"     << (v.deleted ? "true" : "false")
      << "}";
    return o.str();
}

// ── Commands ──────────────────────────────────────────────────────────────────

static void cmd_info(const std::string& hive_path) {
    auto hive = regbroker::core::Hive::open(hive_path);
    if (!hive) {
        std::cout << "{\"error\":\"Cannot open hive\"}\n";
        return;
    }
    const auto& info = hive->info();
    auto root = hive->root();
    std::cout << "{"
              << "\"file\":"         << jstr(hive_path) << ","
              << "\"version\":"      << jstr(std::to_string(info.major_version) + "." + std::to_string(info.minor_version)) << ","
              << "\"major\":"        << info.major_version << ","
              << "\"minor\":"        << info.minor_version << ","
              << "\"timestamp\":"    << jstr(regbroker::core::Decoder::filetime_to_string(info.timestamp)) << ","
              << "\"timestamp_unix\":" << regbroker::core::Decoder::filetime_to_unix(info.timestamp) << ","
              << "\"hive_size\":"    << info.hive_data_size << ","
              << "\"root_name\":"    << jstr(info.root_key_name) << ","
              << "\"root_subkeys\":" << (root ? root->num_subkeys : 0) << ","
              << "\"root_values\":"  << (root ? root->num_values  : 0)
              << "}\n";
}

static void cmd_ls(const std::string& hive_path, const std::string& path) {
    auto hive = regbroker::core::Hive::open(hive_path);
    if (!hive) { std::cout << "{\"error\":\"Cannot open hive\"}\n"; return; }

    auto key = hive->get_key(path);
    if (!key) { std::cout << "{\"error\":\"Key not found: " + json_escape(path) + "\"}\n"; return; }

    auto subkeys = hive->list_subkeys(*key);
    auto values  = hive->list_values(*key);

    std::cout << "{\"key\":" << jkey_to_obj(*key) << ",\"subkeys\":[";
    for (size_t i = 0; i < subkeys.size(); ++i) {
        if (i) std::cout << ",";
        std::cout << jkey_to_obj(subkeys[i]);
    }
    std::cout << "],\"values\":[";
    for (size_t i = 0; i < values.size(); ++i) {
        if (i) std::cout << ",";
        std::cout << jvalue_to_obj(values[i]);
    }
    std::cout << "]}\n";
}

static void cmd_cat(const std::string& hive_path, const std::string& path,
                    const std::string& value_name) {
    auto hive = regbroker::core::Hive::open(hive_path);
    if (!hive) { std::cout << "{\"error\":\"Cannot open hive\"}\n"; return; }

    auto key = hive->get_key(path);
    if (!key) { std::cout << "{\"error\":\"Key not found\"}\n"; return; }

    auto val = hive->get_value(*key, value_name);
    if (!val) { std::cout << "{\"error\":\"Value not found\"}\n"; return; }

    // Full hex dump for large values
    std::string full_hex;
    full_hex.reserve(val->data.size() * 2);
    for (uint8_t b : val->data) {
        char buf[4];
        snprintf(buf, sizeof(buf), "%02x", b);
        full_hex += buf;
    }

    std::cout << "{\"value\":" << jvalue_to_obj(*val)
              << ",\"hex_full\":" << jstr(full_hex)
              << ",\"hex_dump\":" << jstr(regbroker::core::Decoder::hex_dump(val->data))
              << "}\n";
}

static void cmd_tree(const std::string& hive_path, const std::string& path,
                     int max_depth) {
    auto hive = regbroker::core::Hive::open(hive_path);
    if (!hive) { std::cout << "{\"error\":\"Cannot open hive\"}\n"; return; }

    auto key = hive->get_key(path);
    if (!key) { std::cout << "{\"error\":\"Key not found\"}\n"; return; }

    std::cout << "[";
    bool first = true;
    hive->traverse(*key, [&](const regbroker::core::RegKey& k, int depth) -> bool {
        if (!first) std::cout << ",";
        first = false;
        // Inline values for leaves
        std::cout << "{\"key\":" << jkey_to_obj(k) << ",\"depth\":" << depth;
        if (depth <= max_depth) {
            auto vals = hive->list_values(k);
            if (!vals.empty()) {
                std::cout << ",\"values\":[";
                for (size_t i = 0; i < vals.size(); ++i) {
                    if (i) std::cout << ",";
                    std::cout << jvalue_to_obj(vals[i]);
                }
                std::cout << "]";
            }
        }
        std::cout << "}";
        return true;
    }, max_depth);
    std::cout << "]\n";
}

static void cmd_find(const std::string& hive_path, const std::string& start_path,
                     const std::string& pattern) {
    auto hive = regbroker::core::Hive::open(hive_path);
    if (!hive) { std::cout << "{\"error\":\"Cannot open hive\"}\n"; return; }

    auto key = hive->get_key(start_path);
    if (!key) { std::cout << "{\"error\":\"Start path not found\"}\n"; return; }

    // Case-insensitive substring match
    auto lower = [](std::string s) { for (auto& c : s) c = tolower(c); return s; };
    std::string pat_low = lower(pattern);

    std::cout << "[";
    bool first = true;
    hive->traverse(*key, [&](const regbroker::core::RegKey& k, int /*depth*/) -> bool {
        if (lower(k.name).find(pat_low) != std::string::npos) {
            if (!first) std::cout << ",";
            first = false;
            std::cout << jkey_to_obj(k);
        }
        return true;
    });
    std::cout << "]\n";
}

static void cmd_search(const std::string& hive_path, const std::string& start_path,
                       const std::string& pattern) {
    auto hive = regbroker::core::Hive::open(hive_path);
    if (!hive) { std::cout << "{\"error\":\"Cannot open hive\"}\n"; return; }

    auto key = hive->get_key(start_path);
    if (!key) { std::cout << "{\"error\":\"Start path not found\"}\n"; return; }

    auto lower = [](std::string s) { for (auto& c : s) c = tolower(c); return s; };
    std::string pat_low = lower(pattern);

    std::cout << "[";
    bool first = true;
    hive->traverse(*key, [&](const regbroker::core::RegKey& k, int /*depth*/) -> bool {
        for (auto& v : hive->list_values(k)) {
            std::string decoded = regbroker::core::Decoder::to_string(v);
            if (lower(v.name).find(pat_low) != std::string::npos ||
                lower(decoded).find(pat_low) != std::string::npos) {
                if (!first) std::cout << ",";
                first = false;
                std::cout << "{\"key\":" << jkey_to_obj(k) << ",\"value\":" << jvalue_to_obj(v) << "}";
            }
        }
        return true;
    });
    std::cout << "]\n";
}

static void cmd_recover(const std::string& hive_path) {
    auto hive = regbroker::core::Hive::open(hive_path);
    if (!hive) { std::cout << "{\"error\":\"Cannot open hive\"}\n"; return; }

    regbroker::core::RecoveryEngine engine(*hive);
    auto report = engine.scan();

    std::cout << "{"
              << "\"hbins_scanned\":"      << report.hbins_scanned      << ","
              << "\"free_cells_scanned\":" << report.free_cells_scanned << ","
              << "\"total_free_bytes\":"   << report.total_free_bytes   << ","
              << "\"deleted_keys\":["      ;
    for (size_t i = 0; i < report.keys.size(); ++i) {
        if (i) std::cout << ",";
        std::cout << "{"
                  << "\"key\":"            << jkey_to_obj(report.keys[i].key)    << ","
                  << "\"hbin_offset\":"    << report.keys[i].hbin_offset          << ","
                  << "\"parent_reachable\":" << (report.keys[i].parent_reachable ? "true" : "false") << ","
                  << "\"reason\":"         << jstr(report.keys[i].reason)
                  << "}";
    }
    std::cout << "],\"deleted_values\":[";
    for (size_t i = 0; i < report.values.size(); ++i) {
        if (i) std::cout << ",";
        std::cout << "{"
                  << "\"value\":"       << jvalue_to_obj(report.values[i].value) << ","
                  << "\"hbin_offset\":" << report.values[i].hbin_offset          << ","
                  << "\"data_intact\":" << (report.values[i].data_intact ? "true" : "false") << ","
                  << "\"reason\":"      << jstr(report.values[i].reason)
                  << "}";
    }
    std::cout << "]}\n";
}

// ── Entry point ───────────────────────────────────────────────────────────────

static void usage() {
    std::cerr <<
        "regbroker-core — Windows Registry Hive Parser\n\n"
        "Usage:\n"
        "  regbroker-core info    <hive>\n"
        "  regbroker-core ls      <hive> <path>\n"
        "  regbroker-core cat     <hive> <path> <value>\n"
        "  regbroker-core tree    <hive> <path> [depth]\n"
        "  regbroker-core find    <hive> <path> <pattern>\n"
        "  regbroker-core search  <hive> <path> <pattern>\n"
        "  regbroker-core recover <hive>\n\n"
        "All output is JSON on stdout.\n";
}

int main(int argc, char* argv[]) {
    if (argc < 3) { usage(); return 1; }

    std::string cmd  = argv[1];
    std::string hive = argv[2];

    if (cmd == "info") {
        cmd_info(hive);
    } else if (cmd == "ls" && argc >= 4) {
        cmd_ls(hive, argv[3]);
    } else if (cmd == "cat" && argc >= 5) {
        cmd_cat(hive, argv[3], argv[4]);
    } else if (cmd == "tree" && argc >= 4) {
        int depth = (argc >= 5) ? std::stoi(argv[4]) : 3;
        cmd_tree(hive, argv[3], depth);
    } else if (cmd == "find" && argc >= 5) {
        cmd_find(hive, argv[3], argv[4]);
    } else if (cmd == "search" && argc >= 5) {
        cmd_search(hive, argv[3], argv[4]);
    } else if (cmd == "recover") {
        cmd_recover(hive);
    } else {
        usage();
        return 1;
    }
    return 0;
}
