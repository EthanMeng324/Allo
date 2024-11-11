# Copyright Allo authors. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
# pylint: disable=bad-builtin

from .utils import format_str
from ..ir.transform import find_func_in_module
from ..utils import get_func_inputs_outputs, get_clostest_pow2

header = """
//=============================================================================
// Auto generated by Allo
//=============================================================================

#include <iostream>
#include <vector>
#include <fstream>
#include <cstdio>

#include <gflags/gflags.h>
#include <tapa.h>

using std::clog;
using std::endl;
using std::vector;

DEFINE_string(bitstream, "", "path to bitstream file, run csim if empty");

template <typename T>
struct aligned_allocator {
    using value_type = T;

    aligned_allocator() {}

    aligned_allocator(const aligned_allocator&) {}

    template <typename U>
    aligned_allocator(const aligned_allocator<U>&) {}

    T* allocate(std::size_t num) {
        void* ptr = nullptr;

#if defined(_WINDOWS)
        {
            ptr = _aligned_malloc(num * sizeof(T), 4096);
            if (ptr == nullptr) {
                std::cout << "Failed to allocate memory" << std::endl;
                exit(EXIT_FAILURE);
            }
        }
#else
        {
            if (posix_memalign(&ptr, 4096, num * sizeof(T))) throw std::bad_alloc();
        }
#endif
        return reinterpret_cast<T*>(ptr);
    }
    void deallocate(T* p, std::size_t num) {
#if defined(_WINDOWS)
        _aligned_free(p);
#else
        free(p);
#endif
    }
};
"""

main_header = """
int main(int argc, char* argv[]) {
    gflags::ParseCommandLineFlags(&argc, &argv, /*remove_flags=*/true);
"""

ctype_map = {
    "f32": "float",
    "f64": "double",
    "i8": "char",
    "i16": "short",
    "i32": "int",
    "i64": "long",
    "i128": "__int128_t",  # unverified
    "ui1": "bool",
    "ui8": "unsigned char",
    "ui16": "unsigned short",
    "ui32": "unsigned int",
    "ui64": "unsigned long",
}


def codegen_tapa_host(top, module, hls_code):
    # Reference: https://github.com/rapidstream-org/rapidstream-tapa/blob/main/tests/apps/vadd/vadd-host.cpp
    func = find_func_in_module(module, top)
    inputs, outputs = get_func_inputs_outputs(func)
    in_dtypes = []
    in_names = []
    out_dtypes = []
    out_names = []
    
    out_str = format_str(header, indent=0, strip=False)
    
    # generate declaration for top
    func_decl = False
    for line in hls_code.split("\n"):
        if line.startswith(f"void {top}"):
            func_decl = True
            out_str += line + "\n"
        elif func_decl and line.startswith(") {"):
            func_decl = False
            out_str += ");\n"
            break
        elif func_decl:
            arg_type = line.strip()
            _, var = arg_type.rsplit(" ", 1)
            comma = "," if var[-1] == "," else ""
            ele_type = arg_type.split("[")[0].split(" ")[0].strip()
            if "[" in var:  # array
                var = var.split("[")[0]
                out_str += "    " + ele_type + " *" + var + f"{comma}\n"
            else:  # scalar
                var = var.split(",")[0]
                out_str += "    " + ele_type + " " + var + f"{comma}\n"
                
    out_str += format_str(main_header, indent=0, strip=False)
                
    # Generate in/out buffers
    for i, (in_dtype, in_shape) in enumerate(inputs):
        if in_dtype in ctype_map:
            in_dtype = ctype_map[in_dtype]
        elif in_dtype.startswith("i") or in_dtype.startswith("ui"):
            prefix, bitwidth = in_dtype.split("i")
            if int(bitwidth) == 1:
                in_dtype = "bool"
            else:
                new_int_type = f"{prefix}i{max(get_clostest_pow2(int(bitwidth)), 8)}"
                in_dtype = ctype_map[new_int_type]
        elif in_dtype.startswith("fixed") or in_dtype.startswith("ufixed"):
            in_dtype = "float"
        else:
            raise ValueError(f"Unsupported input type: {in_dtype}")
        out_str += format_str(f'std::ifstream ifile{i}("input{i}.data");')
        out_str += format_str(f"if (!ifile{i}.is_open()) {{")
        out_str += format_str(
            '  std::cerr << "Error: Could not open input file.\\n";', strip=False
        )
        out_str += format_str("  return 1;", strip=False)
        out_str += format_str("}")
        in_shape = [str(i) for i in in_shape]
        if len(in_shape) == 0:
            # scalar
            out_str += format_str(f"{in_dtype} source_in{i};")
            out_str += format_str(f"ifile{i} >> source_in{i};")
        else:
            out_str += format_str(
                f"{in_dtype} in_data_{i}[{'*'.join(map(str, in_shape))}];"
            )
            out_str += format_str(
                f"for (unsigned i = 0; i < {'*'.join(map(str, in_shape))}; i++) {{"
            )
            out_str += format_str(f"  ifile{i} >> in_data_{i}[i];", strip=False)
            out_str += format_str("}")
            out_str += format_str(
                f"size_t size_bytes_in{i} = sizeof({in_dtype}) * {' * '.join(in_shape)};",
                strip=False,
            )
            out_str += format_str(
                f"std::vector<{in_dtype}, aligned_allocator<{in_dtype}> > source_in{i}(in_data_{i}, in_data_{i} + {' * '.join(in_shape)});",
                strip=False,
            )
        in_dtypes.append(in_dtype)
        in_names.append(f"source_in{i}")
    for i, (out_dtype, out_shape) in enumerate(outputs):
        if out_dtype in ctype_map:
            out_dtype = ctype_map[out_dtype]
        elif out_dtype.startswith("i") or out_dtype.startswith("ui"):
            prefix, bitwidth = out_dtype.split("i")
            new_int_type = f"{prefix}i{max(get_clostest_pow2(int(bitwidth)), 8)}"
            out_dtype = ctype_map[new_int_type]
        elif out_dtype.startswith("fixed") or out_dtype.startswith("ufixed"):
            out_dtype = "float"
        else:
            raise ValueError(f"Unsupported input type: {out_dtype}")
        out_shape = [str(i) for i in out_shape]
        out_str += format_str(
            f"size_t size_bytes_out{i} = sizeof({out_dtype}) * {' * '.join(out_shape)};\n",
            strip=False,
        )
        out_str += format_str(
            f"std::vector<{out_dtype}, aligned_allocator<{out_dtype}> > source_out{i}({' * '.join(out_shape)});\n",
            strip=False,
        )
        out_str += format_str(
            f"std::fill(source_out{i}.begin(), source_out{i}.end(), 0);\n", strip=False
        )
        out_dtypes.append(out_dtype)
        out_names.append(f"source_out{i}")
    out_str += "\n"
    # generate tapa invoke
    out_str += f"""    int64_t kernel_time_ns = tapa::invoke(
        {top}, FLAGS_bitstream,
"""
    # TODO: can change to read_only or write_only if needed
    for i, (in_dtype, in_name) in enumerate(zip(in_dtypes, in_names)):
        out_str += f"        tapa::read_write_mmap<{in_dtype}>({in_name})"
        if len(out_dtypes) == 0 and i == len(in_dtypes) - 1:
            out_str += "\n"
        else:
            out_str += ",\n"
    for i, (out_dtype, out_name) in enumerate(zip(out_dtypes, out_names)):
        out_str += f"        tapa::read_write_mmap<{out_dtype}>({out_name})"
        if i == len(out_dtypes) - 1:
            out_str += "\n"
        else:
            out_str += ",\n"
    out_str += "    );\n"
    out_str += "    clog << \"kernel time: \" << kernel_time_ns * 1e-9 << \" s\" << endl;\n\n"
    assert len(outputs) <= 1, "Only support one output for now"
    if len(outputs) == 0:
        out_buf = "source_in" + str(len(inputs) - 1)
    else:
        out_buf = "source_out" + str(len(outputs) - 1)
    out_str += format_str(
        f"""    // Write the output data to file
    std::ofstream ofile;
    ofile.open("output.data");
    if (!ofile) {{
        std::cerr << "Failed to open output file!" << std::endl;
        return EXIT_FAILURE;
    }}
    for (unsigned i = 0; i < {out_buf}.size(); i++) {{
        ofile << {out_buf}[i] << std::endl;
    }}
    ofile.close();
    """,
        strip=False,
        indent=0,
    )
    out_str += format_str("return EXIT_SUCCESS;", strip=False)
    out_str += "}\n"
    return out_str