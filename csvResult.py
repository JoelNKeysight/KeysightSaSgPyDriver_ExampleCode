
def open_csv(result_file_name):
    writer = open(result_file_name, 'w')
    return writer

def write_header(writer, header_str):
    writer.write(f"{header_str}\n")

def write_row(writer, result_str):
    writer.write(f"{result_str}\n")

def close_csv(writer):
    writer.close()

