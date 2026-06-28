#import "Basic";
#import "Compiler";
#import "String";

#run {
    set_build_options_dc(.{do_output=false});

    tests := string.[
        "tests/test_ledger_core.jai",
    ];

    for file: tests {
        w := compiler_create_workspace(tprint("Leira Jai %", file));
        if !w {
            print("Failed to create workspace for %\n", file);
            continue;
        }

        options := get_build_options(w);
        options.output_path = "tests";

        basename := file;
        slash := find_index_from_right(file, #char "/");
        if slash != -1 {
            basename = slice(file, slash + 1, file.count - (slash + 1));
        }

        dot := find_index_from_right(basename, #char ".");
        if dot != -1 {
            basename = slice(basename, 0, dot);
        }

        options.output_executable_name = basename;
        set_build_options(options, w);
        add_build_file(file, w);
    }
}

