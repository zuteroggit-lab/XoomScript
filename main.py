import sys
import re
import ctypes
import subprocess
import os
from llvmlite import ir, binding

# --- 1. НАСТРОЙКА ОКРУЖЕНИЯ ---
# Загружаем C-библиотеку для printf/scanf
if os.name == 'nt':  # Windows
    try:
        ctypes.CDLL("msvcrt.dll")
    except:
        pass
else:  # Linux/Mac
    ctypes.CDLL("libc.so.6")

# --- 2. ИНИЦИАЛИЗАЦИЯ LLVM ---
def init_llvm():
    binding.initialize_all_targets()
    binding.initialize_all_asmprinters()
    binding.initialize_native_target()
    binding.initialize_native_asmprinter()
    
    module = ir.Module(name="xoom_core")
    module.triple = binding.get_default_triple()
    return module

module = init_llvm()
func_type = ir.FunctionType(ir.IntType(64), [])
main_func = ir.Function(module, func_type, name="main")
entry_block = main_func.append_basic_block(name="entry")
builder = ir.IRBuilder(entry_block)

# Типы данных
voidptr_ty = ir.IntType(8).as_pointer()
printf_ty = ir.FunctionType(ir.IntType(32), [voidptr_ty], var_arg=True)
printf = ir.Function(module, printf_ty, name="printf")
scanf_ty = ir.FunctionType(ir.IntType(32), [voidptr_ty], var_arg=True)
scanf = ir.Function(module, scanf_ty, name="scanf")

# --- 3. РЕГИСТРЫ И ПАМЯТЬ ---
# Целочисленные регистры
regs = {name: builder.alloca(ir.IntType(64), name=name) 
        for name in ['rax', 'rbx', 'rcx', 'rdx', 'rsp', 'r8', 'r9', 'r10']}

# Дробные регистры (XMM)
for i in range(16):
    name = f'xmm{i}'
    regs[name] = builder.alloca(ir.DoubleType(), name=name)

# Хранилище для меток переходов
mentos_blocks = {}

# --- 4. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (Move, Add, Sub) ---
def generate_sub(cmd, d_type, sources, dests):
    for s, d in zip(sources, dests):
        if d_type == 'int':
            curr = builder.load(regs[d])
            val = ir.Constant(ir.IntType(64), int(s)) if s.isdigit() else builder.load(regs[s])
            res = builder.sub(curr, val)
            builder.store(res, regs[d])
        elif d_type == 'dbl':
            curr = builder.load(regs[d])
            val = ir.Constant(ir.DoubleType(), float(s)) if '.' in s else builder.load(regs[s])
            res = builder.fsub(curr, val)
            builder.store(res, regs[d])

def generate_add(cmd, d_type, sources, dests):
    for s, d in zip(sources, dests):
        if d_type == 'int':
            curr = builder.load(regs[d])
            val = ir.Constant(ir.IntType(64), int(s)) if s.isdigit() else builder.load(regs[s])
            res = builder.add(curr, val)
            builder.store(res, regs[d])
        elif d_type == 'dbl':
            curr = builder.load(regs[d])
            val = ir.Constant(ir.DoubleType(), float(s)) if '.' in s else builder.load(regs[s])
            res = builder.fadd(curr, val)
            builder.store(res, regs[d])

def generate_move(cmd, d_type, sources, dests):
    for s, d in zip(sources, dests):
        if d_type == 'int':
            if s.isdigit():
                val = ir.Constant(ir.IntType(64), int(s))
            else:
                val = builder.load(regs[s])
            builder.store(val, regs[d])
        elif d_type == 'dbl':
            try:
                val = ir.Constant(ir.DoubleType(), float(s))
            except ValueError:
                val = builder.load(regs[s])
            builder.store(val, regs[d])
        elif d_type == 'str':
            string_val = ir.Constant(ir.ArrayType(ir.IntType(8), len(s) + 1),
                                     bytearray(s.encode("utf8")) + b"\x00")
            global_str = ir.GlobalVariable(module, string_val.type, name=f"str_{hash(s)}")
            global_str.initializer = string_val
            ptr = builder.bitcast(global_str, ir.IntType(64))
            builder.store(ptr, regs[d])

# --- 5. ОСНОВНОЙ КОМПИЛЯТОР СТРОКИ ---
def compile_line(line):
    line = line.split('###')[0].strip()
    if not line: return
    line = line.replace(';', '')

    # --- CLEAR ---
    if 'clear' in line:
        targets = re.findall(r'\[(.*?)\]', line)
        if targets:
            regs_to_clear = [r.strip() for r in targets[0].split(',')]
        else:
            regs_to_clear = [line.replace('clear', '').strip()]
        
        for r in regs_to_clear:
            if r in regs:
                builder.store(ir.Constant(ir.IntType(64), 0), regs[r])

    # --- CHECK ---
    elif 'check' in line and '(' in line:
        try:
            parts = line.split('=')
            condition_part = parts[0].strip()
            action_part = parts[1].strip()
            cond_content = condition_part[condition_part.find("(")+1:condition_part.rfind(")")]
            match = re.search(r'([a-z0-9]+)\s*(==|!=|>=|<=|>|<)\s*(\d+)', cond_content)
            
            if match:
                left_reg, op, right_val = match.group(1).strip(), match.group(2), int(match.group(3).strip())
                reg_val = builder.load(regs[left_reg])
                target_val = ir.Constant(ir.IntType(64), right_val)
                
                if op == '==': is_true = builder.icmp_signed('==', reg_val, target_val)
                elif op == '!=': is_true = builder.icmp_signed('!=', reg_val, target_val)
                elif op == '>=': is_true = builder.icmp_signed('>=', reg_val, target_val)
                elif op == '<=': is_true = builder.icmp_signed('<=', reg_val, target_val)
                elif op == '>':  is_true = builder.icmp_signed('>', reg_val, target_val)
                elif op == '<':  is_true = builder.icmp_signed('<', reg_val, target_val)
                else: is_true = builder.icmp_signed('==', reg_val, target_val) # fallback

                then_block = builder.append_basic_block(f"then_{hash(line)}")
                else_block = builder.append_basic_block(f"else_{hash(line)}")
                builder.cbranch(is_true, then_block, else_block)
                
                builder.position_at_end(then_block)
                compile_line(action_part) # Рекурсия
                builder.branch(else_block)
                builder.position_at_end(else_block)
        except Exception as e:
            print(f" [Ошибка check] {e}")

    # --- METKI (Jumps) ---
    elif 'set mento=' in line:
        label_name = line.split('=')[1].strip()
        new_block = builder.append_basic_block(label_name)
        builder.branch(new_block)
        builder.position_at_end(new_block)
        mentos_blocks[label_name] = new_block

    elif 'jump mentos=' in line:
        target_name = line.split('=')[1].strip()
        if target_name in mentos_blocks:
            builder.branch(mentos_blocks[target_name])
            after_jump = builder.append_basic_block(f"post_{target_name}")
            builder.position_at_end(after_jump)

    # --- INPUT ---
    elif 'input(' in line:
        msg = re.findall(r'"(.*?)"', line)[0] + "\0"
        c_msg = ir.Constant(ir.ArrayType(ir.IntType(8), len(msg)), bytearray(msg.encode("utf8")))
        g_msg = ir.GlobalVariable(module, c_msg.type, name=f"i_{hash(line)}")
        g_msg.initializer = c_msg
        builder.call(printf, [builder.bitcast(g_msg, voidptr_ty)])

    elif 'input_save >>' in line:
        target = line.split('>>')[1].strip()
        fmt = "%lld\0"
        c_fmt = ir.Constant(ir.ArrayType(ir.IntType(8), len(fmt)), bytearray(fmt.encode("utf8")))
        g_fmt = ir.GlobalVariable(module, c_fmt.type, name=f"f_{hash(line)}")
        g_fmt.initializer = c_fmt
        builder.call(scanf, [builder.bitcast(g_fmt, voidptr_ty), regs[target]])

    # --- PRINTCONSOLE ---
    elif 'printconsole' in line:
        content = line[line.find("(")+1:line.rfind(")")]
        args_raw = re.findall(r'".*?"|[\w\+\-\*/]+', content)
        format_str = ""
        llvm_params = []
        for arg in args_raw:
            if arg.startswith('"'):
                format_str += arg.strip('"')
            else:
                if format_str and not format_str.endswith(' '): format_str += " "
                if any(op in arg for op in ['+', '-', '*', '/']):
                    match = re.match(r'(\d+)([\+\-\*/])(\d+)', arg)
                    if match:
                        v1 = ir.Constant(ir.IntType(64), int(match.group(1)))
                        op = match.group(2)
                        v2 = ir.Constant(ir.IntType(64), int(match.group(3)))
                        if op == '+': res = builder.add(v1, v2)
                        elif op == '-': res = builder.sub(v1, v2)
                        elif op == '*': res = builder.mul(v1, v2)
                        elif op == '/': res = builder.sdiv(v1, v2)
                        format_str += "%lld"
                        llvm_params.append(res)
                elif arg in regs:
                    format_str += "%lld"
                    llvm_params.append(builder.load(regs[arg]))
                else:
                    format_str += f"{arg}"
        format_str += "\n\0"
        c_str = ir.Constant(ir.ArrayType(ir.IntType(8), len(format_str)), bytearray(format_str.encode("utf8")))
        g_str = ir.GlobalVariable(module, c_str.type, name=f"p_{hash(line)}")
        g_str.initializer = c_str
        builder.call(printf, [builder.bitcast(g_str, voidptr_ty)] + llvm_params)

    # --- MULE ---
    elif 'mule' in line:
        parts = line.split()
        val = int(parts[2])
        target = line.split('**>>')[1].strip()
        old_val = builder.load(regs[target])
        res = builder.mul(old_val, ir.Constant(ir.IntType(64), val))
        builder.store(res, regs[target])

    # --- MOVE / ADD / SUB ---
    elif '>>' in line: 
        parts = re.split(r'\s+', line, maxsplit=2)
        cmd, d_type = parts[0].lower(), parts[1].lower()
        payload = parts[2].split('>>')
        srcs = [s.strip().strip('[]') for s in payload[0].split(',')]
        dsts = [d.strip().strip('[]') for d in payload[1].split(',')]
        if cmd == 'move': generate_move(cmd, d_type, srcs, dsts)
        elif cmd == 'add': generate_add(cmd, d_type, srcs, dsts)

    elif '<<' in line:
        parts = re.split(r'\s+', line, maxsplit=2)
        cmd, d_type = parts[0].lower(), parts[1].lower()
        payload = parts[2].split('<<')
        srcs = [s.strip().strip('[]') for s in payload[0].split(',')]
        dsts = [d.strip().strip('[]') for d in payload[1].split(',')]
        if cmd == 'sub': generate_sub(cmd, d_type, srcs, dsts)

# --- 6. ФУНКЦИИ ЗАПУСКА И СБОРКИ ---
def run_jit():
    builder.ret(builder.load(regs['rax'])) 
    llvm_ir = str(module)
    mod = binding.parse_assembly(llvm_ir)
    target = binding.Target.from_default_triple()
    target_machine = target.create_target_machine()
    engine = binding.create_mcjit_compiler(mod, target_machine)
    engine.finalize_object()
    func_ptr = engine.get_function_address("main")
    cfunc = ctypes.CFUNCTYPE(ctypes.c_int64)(func_ptr)
    result = cfunc()
    
    print("\n" + "═"*50)
    print(f"   XOOM COMPILER v0.1.9 DEBUG REPORT   ")
    print("═"*50)
    print(f" Режим          : JIT Execution")
    print(f" Итог в RAX     : {result}")
    print(f" Статус         : SUCCESS")
    print("═"*50 + "\n")

def build_exe(file_name_prefix):
    # Завершаем main корректно
    builder.ret(ir.Constant(ir.IntType(64), 0))
    
    print(" [Инфо] Генерация объектного файла...")
    
    target = binding.Target.from_default_triple()
    target_machine = target.create_target_machine()
    
    llvm_ir = str(module)
    mod = binding.parse_assembly(llvm_ir)
    mod.verify()
    
    obj_data = target_machine.emit_object(mod)
    obj_file = f"{file_name_prefix}.o"
    
    with open(obj_file, "wb") as f:
        f.write(obj_data)
        
    print(f" [Инфо] Линковка {file_name_prefix}.exe с помощью Clang...")
    
    # Для Windows нужна линковка с msvcrt
    cmd = f"clang {obj_file} -o {file_name_prefix}.exe"
    result = subprocess.run(cmd, shell=True)
    
    if result.returncode == 0:
        print(f" [УСПЕХ] Файл собран: {file_name_prefix}.exe")
        try: os.remove(obj_file) 
        except: pass
    else:
        print(" [ОШИБКА] Не удалось собрать EXE. Убедитесь, что Clang установлен.")

# --- 7. ГЛАВНОЕ МЕНЮ ---
def main():
    if len(sys.argv) < 2:
        print("Использование: python main.py [run/build] [файл.xs] [опции]")
        return

    command = sys.argv[1]

    if command == "--version":
        print("XoomScript Compiler v0.1.9")
        print("Автор: Zuterog")
        print("Фичи: JIT, EXE Build, Math Print, Logic Checks, Clear")
        return

    # Обработка run или build
    if command in ["run", "build"]:
        if len(sys.argv) < 3:
            print("Ошибка: Укажите путь к файлу .xs")
            return
            
        file_path = os.path.abspath(sys.argv[2])
        if not os.path.exists(file_path):
            print(f"Файл не найден: {file_path}")
            return

        # Чтение и компиляция строк
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            print(f" [Xoom 0.1.9] Сборка {command}...")
            for line in lines:
                try:
                    compile_line(line)
                except Exception as e:
                    print(f" [Критическая ошибка строки] {e}")
                    return
            
            # Выбор действия
            if command == "run":
                run_jit()
            elif command == "build":
                # Ищем аргумент name= (например: python main.py build main.xs name=Game)
                out_name = "output"
                for arg in sys.argv:
                    if "name=" in arg:
                        out_name = arg.split("=")[1]
                build_exe(out_name)
                
        except Exception as e:
            print(f" [Ошибка] {e}")

if __name__ == "__main__":
    main()