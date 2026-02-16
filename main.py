<<<<<<< HEAD
import sys
import re
import ctypes
import subprocess
import os
from llvmlite import ir, binding

# ==========================================
#   XOOM COMPILER v0.2.0 (Architecture Update)
#   Features: Variables, Strings, Functions, Types
# ==========================================

# --- 1. НАСТРОЙКА ОКРУЖЕНИЯ ---
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
    module = ir.Module(name="xoom_core_020")
    module.triple = binding.get_default_triple()
    return module

module = init_llvm()

LIB_DIR = "libraries"
if not os.path.exists(LIB_DIR):
    os.makedirs(LIB_DIR)

def process_includes(lines):
    """Парсер для подключения библиотек"""
    final_code = []
    for line in lines:
        if line.startswith("#include"):
            # Извлекаем имя: #include <math> -> math
            lib_name = re.search(r'<(.*?)>', line).group(1)
            lib_path = os.path.join(LIB_DIR, f"{lib_name}.xs")
            if os.path.exists(lib_path):
                with open(lib_path, 'r', encoding='utf-8') as f:
                    final_code.extend(f.readlines())
                print(f" [Библиотека] Подключена: {lib_name}")
            else:
                print(f" [Ошибка] Библиотека {lib_name} не найдена в {LIB_DIR}")
        else:
            final_code.append(line)
    return final_code

# Типы данных LLVM
int64 = ir.IntType(64)
dbl_t = ir.DoubleType()
voidptr = ir.IntType(8).as_pointer()
void_ty = ir.VoidType()

# Внешние функции C
printf_ty = ir.FunctionType(ir.IntType(32), [voidptr], var_arg=True)
printf = ir.Function(module, printf_ty, name="printf")
scanf_ty = ir.FunctionType(ir.IntType(32), [voidptr], var_arg=True)
scanf = ir.Function(module, scanf_ty, name="scanf")

# Создание MAIN функции
main_ty = ir.FunctionType(int64, [])
main_func = ir.Function(module, main_ty, name="main")
main_entry = main_func.append_basic_block(name="entry")
main_builder = ir.IRBuilder(main_entry)

# --- 3. ГЛОБАЛЬНОЕ СОСТОЯНИЕ ---
# Текущий строитель (может меняться внутри функций)
builder = main_builder

# Хранилище переменных: { 'name': {'ptr': pointer, 'type': 'int'/'str'/'dbl'} }
variables = {}

# Хранилище пользовательских функций: { 'name': function_object }
custom_functions = {}

# Хранилище меток для прыжков (старая логика)
mentos_blocks = {}

# --- 4. ИНИЦИАЛИЗАЦИЯ СИСТЕМНЫХ РЕГИСТРОВ (Backward Compatibility) ---
# Мы добавляем их в variables, чтобы они работали как обычные int/dbl переменные
def init_registers():
    # Int registers
    for r in ['rax', 'rbx', 'rcx', 'rdx', 'rsp', 'r8', 'r9', 'r10']:
        ptr = main_builder.alloca(int64, name=r)
        main_builder.store(ir.Constant(int64, 0), ptr)
        variables[r] = {'ptr': ptr, 'type': 'int'}
    
    # Double registers
    for i in range(16):
        r = f'xmm{i}'
        ptr = main_builder.alloca(dbl_t, name=r)
        main_builder.store(ir.Constant(dbl_t, 0.0), ptr)
        variables[r] = {'ptr': ptr, 'type': 'dbl'}

init_registers()

# --- 5. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def get_var(name):
    """Получить переменную. Если нет - создать int по умолчанию (для легаси)."""
    if name in variables:
        return variables[name]
    # Авто-создание неизвестной переменной как int (для совместимости)
    ptr = builder.alloca(int64, name=name)
    variables[name] = {'ptr': ptr, 'type': 'int'}
    return variables[name]

def create_string_constant(text):
    """Создает глобальную строку для printf/scanf"""
    text += "\0"
    c_str = ir.Constant(ir.ArrayType(ir.IntType(8), len(text)), bytearray(text.encode("utf8")))
    g_str = ir.GlobalVariable(module, c_str.type, name=f"str_{hash(text)}")
    g_str.initializer = c_str
    return builder.bitcast(g_str, voidptr)

# --- 6. ЯДРО КОМПИЛЯЦИИ ---
def compile_line(line):
    global builder, main_builder # Важно: builder меняется при func
    
    line = line.split('###')[0].strip()
    if not line: return
    line = line.replace(';', '')

    # ==========================
    # НОВЫЕ ФУНКЦИИ (v0.2.0)
    # ==========================

    # --- 1. СПИСКИ (set list int name = {1,2,3}) ---

    if 'set list' in line and '{' in line:
        # Синтаксис: set list name = {10, "hero", 5.5}
        m = re.search(r'set list (\w+)\s*=\s*\{(.*)\}', line)
        if m:
            l_name, l_vals = m.group(1), m.group(2)
            vals = [v.strip() for v in l_vals.split(',')]
            count = len(vals)
            
            # Создаем массив универсальных указателей (i8*)
            arr_ty = ir.ArrayType(voidptr, count)
            ptr = builder.alloca(arr_ty, name=l_name)
            
            for i, v in enumerate(vals):
                idx = ir.Constant(ir.IntType(32), i)
                item_ptr = builder.gep(ptr, [ir.Constant(ir.IntType(32), 0), idx])
                
                # Определяем тип на лету
                if v.startswith('"'): # Строка
                    val = create_string_constant(v.strip('"'))
                elif '.' in v: # Число с плавающей точкой
                    # Конвертируем dbl в указатель (битовый каст)
                    f_val = ir.Constant(dbl_t, float(v))
                    # Для простоты в v0.2.1 сохраняем только указатели на данные
                    val = builder.alloca(dbl_t)
                    builder.store(f_val, val)
                    val = builder.bitcast(val, voidptr)
                else: # Целое число
                    i_val = ir.Constant(int64, int(v))
                    val = builder.alloca(int64)
                    builder.store(i_val, val)
                    val = builder.bitcast(val, voidptr)
                
                builder.store(val, item_ptr)
            
            variables[l_name] = {'ptr': ptr, 'type': 'mixed_list', 'count': count}
            print(f" [Mixed List] Создан список '{l_name}' ({count} элементов)")

    # --- 1. ОБЪЯВЛЕНИЕ ГЛОБАЛЬНЫХ ПЕРЕМЕННЫХ (v0.2.0) ---
    if 'closall var' in line:
        m = re.search(r'closall var (\w+)\s+(.*?)\s+name=(\w+)', line)
        if m:
            v_type, v_val, v_name = m.group(1), m.group(2).strip('"'), m.group(3)
            
            if v_type == 'int':
                # Создаем глобальную переменную
                gv = ir.GlobalVariable(module, int64, name=v_name)
                gv.initializer = ir.Constant(int64, int(v_val))
                variables[v_name] = {'ptr': gv, 'type': 'int'}
            
            elif v_type == 'dbl':
                gv = ir.GlobalVariable(module, dbl_t, name=v_name)
                gv.initializer = ir.Constant(dbl_t, float(v_val))
                variables[v_name] = {'ptr': gv, 'type': 'dbl'}
            
            elif v_type == 'str':
                # Для строк: создаем глобальный указатель
                initial_data = v_val + "\0"
                c_str = ir.Constant(ir.ArrayType(ir.IntType(8), len(initial_data)), bytearray(initial_data.encode("utf8")))
                g_str_const = ir.GlobalVariable(module, c_str.type, name=f"str_const_{v_name}")
                g_str_const.initializer = c_str
                
                # Сам указатель на строку тоже глобальный
                gv_ptr = ir.GlobalVariable(module, voidptr, name=v_name)
                gv_ptr.initializer = g_str_const.bitcast(voidptr)
                variables[v_name] = {'ptr': gv_ptr, 'type': 'str_ptr'}

            print(f" [v0.2.0] Global variable created: {v_name}")

    # 2. ФУНКЦИИ (func name { ... })
    elif 'func' in line and '{' in line:
        f_name = line.split('func')[1].split('{')[0].strip()
        new_func = ir.Function(module, ir.FunctionType(void_ty, []), name=f_name)
        custom_functions[f_name] = new_func
        # ПЕРЕКЛЮЧАЕМ КОНТЕКСТ
        builder = ir.IRBuilder(new_func.append_basic_block("entry"))
        print(f" [Функция] Начало записи: {f_name}")

    elif line == "endfunc" or line == "} endfunc":
        builder.ret_void()
        builder = main_builder # ВОЗВРАТ В MAIN
        print(f" [Функция] Конец записи")

    elif 'call' in line:
        f_name = line.split('call')[1].strip()
        if f_name in custom_functions:
            builder.call(custom_functions[f_name], [])

    # 3. НОВЫЙ ВВОД (input_save-type >> var)
    elif 'input_save-' in line:
        try:
            v_type = line.split('-')[1].split('>>')[0].strip()
            v_name = line.split('>>')[1].strip()
            var = get_var(v_name)
            
            if v_type == 'int':
                fmt = create_string_constant("%lld")
                builder.call(scanf, [fmt, var['ptr']])
            elif v_type == 'dbl':
                fmt = create_string_constant("%lf")
                builder.call(scanf, [fmt, var['ptr']])
            elif v_type == 'str':
                # Читаем в буфер. Внимание: нужен буфер char[].
                # Если переменная str_ptr, загружаем указатель.
                fmt = create_string_constant("%s")
                ptr_val = builder.load(var['ptr'])
                builder.call(scanf, [fmt, ptr_val])
        except Exception as e:
            print(f"Ошибка ввода: {e}")

    # ==========================
    # СТАРЫЕ ФУНКЦИИ (Совместимость + Обновление)
    # ==========================

    # --- ПРЯМОЕ ПРИСВАИВАНИЕ (name = val) ---
    elif '=' in line and 'closall' not in line and 'check' not in line and 'set' not in line:
        parts = line.split('=')
        v_name = parts[0].strip()
        v_val_raw = parts[1].strip()
        
        if v_name in variables:
            var = variables[v_name]
            if var['type'] == 'int':
                val = ir.Constant(int64, int(v_val_raw)) if v_val_raw.isdigit() else builder.load(variables[v_val_raw]['ptr'])
                builder.store(val, var['ptr'])
            elif var['type'] == 'dbl':
                val = ir.Constant(dbl_t, float(v_val_raw)) if '.' in v_val_raw else builder.load(variables[v_val_raw]['ptr'])
                builder.store(val, var['ptr'])

    # --- CLEAR ---
    elif 'clear' in line:
        target = line.replace('clear', '').replace('[','').replace(']','').strip()
        if target in variables:
            var = variables[target]
            if var['type'] == 'int': builder.store(ir.Constant(int64, 0), var['ptr'])
            elif var['type'] == 'dbl': builder.store(ir.Constant(dbl_t, 0.0), var['ptr'])

    elif 'check' in line and '(' in line:
        # Разбиваем на ветки: check (a==0) = action else (a==1) = action else = action
        parts = re.split(r'\s+else\s+', line)
        merge_block = builder.append_basic_block(f"merge_{hash(line) & 0xFFFF}")
        
        for i, part in enumerate(parts):
            # Ветка с условием: check (x == 0) = ... или (x == 1) = ...
            if '(' in part:
                cond_str = part[part.find("(")+1 : part.find(")")]
                action = part.split('=')[1].strip()
                
                m = re.search(r'(\w+)\s*(==|!=|>|<)\s*(\d+)', cond_str)
                if m:
                    lname, op, rval = m.group(1), m.group(2), int(m.group(3))
                    l_val = builder.load(variables[lname]['ptr'])
                    r_val = ir.Constant(int64, rval)
                    cmp = builder.icmp_signed(op, l_val, r_val)
                    
                    true_bb = builder.append_basic_block(f"t_{i}_{hash(line) & 0xFF}")
                    next_bb = builder.append_basic_block(f"n_{i}_{hash(line) & 0xFF}")
                    
                    builder.cbranch(cmp, true_bb, next_bb)
                    
                    # Пишем код для TRUE
                    builder.position_at_end(true_bb)
                    compile_line(action)
                    if not builder.block.is_terminated:
                        builder.branch(merge_block) # Прыжок в конец после выполнения
                    
                    # Переключаемся на блок ELSE/NEXT для следующей итерации
                    builder.position_at_end(next_bb)
            
            # Финальный ELSE без условия: else = ...
            elif '=' in part:
                action = part.split('=')[1].strip()
                compile_line(action)
                if not builder.block.is_terminated:
                    builder.branch(merge_block)

        # Если мы дошли до конца и не попали ни в одно условие
        if not builder.block.is_terminated:
            builder.branch(merge_block)
            
        # Устанавливаем строителя в финальную точку
        builder.position_at_end(merge_block)

    elif 'get ' in line and '[' in line:
        # Синтаксис: get my_data[0] >> rax
        m = re.search(r'get (\w+)\[(\d+)\]\s*>>\s*(\w+)', line)
        if m:
            l_name, idx_val, target = m.group(1), int(m.group(2)), m.group(3)
            if l_name in variables:
                list_ptr = variables[l_name]['ptr']
                idx = ir.Constant(ir.IntType(32), idx_val)
                
                # Получаем адрес элемента
                item_addr = builder.gep(list_ptr, [ir.Constant(ir.IntType(32), 0), idx])
                ptr_to_data = builder.load(item_addr)
                
                # Кастуем i8* обратно в i64, чтобы положить в регистр
                val = builder.ptrtoint(ptr_to_data, int64)
                builder.store(val, variables[target]['ptr'])
                print(f" [List] Извлечен элемент {idx_val} из {l_name} в {target}")

    # --- PRINTCONSOLE (УМНЫЙ ВЫВОД) ---
    elif 'printconsole' in line:
        content = line[line.find("(")+1:line.rfind(")")]
        # Регулярка разбивает на "строки" и имена_переменных
        parts = re.findall(r'".*?"|[\w\.]+', content)
        
        fmt_str = ""
        args = []
        
        for p in parts:
            if p.startswith('"'):
                fmt_str += p.strip('"')
            elif p in variables:
                v = variables[p]
                if v['type'] == 'int':
                    fmt_str += "%lld"
                    args.append(builder.load(v['ptr']))
                elif v['type'] == 'dbl':
                    fmt_str += "%f"
                    args.append(builder.load(v['ptr']))
                elif v['type'] == 'str_ptr':
                    fmt_str += "%s"
                    args.append(builder.load(v['ptr']))
            elif p.isdigit():
                 fmt_str += "%lld"
                 args.append(ir.Constant(int64, int(p)))
        
        fmt_str += "\n"
        fmt_ptr = create_string_constant(fmt_str)
        builder.call(printf, [fmt_ptr] + args)

    # --- MATH (MOVE, ADD, SUB) ---
    elif '>>' in line or '<<' in line:
        is_move = 'move' in line
        is_add = 'add' in line
        is_sub = 'sub' in line
        
        if is_move or is_add or is_sub:
            parts = re.split(r'>>|<<', line)
            # Пример: move int 10 >> rax
            # Left: move int 10, Right: rax
            left_parts = parts[0].strip().split() # [move, int, 10]
            val_raw = left_parts[-1]
            target_name = parts[1].strip()
            
            var_target = get_var(target_name)
            
            # Определяем значение источника
            if val_raw.isdigit():
                val = ir.Constant(int64, int(val_raw))
            elif val_raw in variables:
                val = builder.load(variables[val_raw]['ptr'])
            else:
                val = ir.Constant(int64, 0) # Fallback

            if is_move:
                builder.store(val, var_target['ptr'])
            elif is_add:
                curr = builder.load(var_target['ptr'])
                res = builder.add(curr, val)
                builder.store(res, var_target['ptr'])
            elif is_sub:
                curr = builder.load(var_target['ptr'])
                res = builder.sub(curr, val)
                builder.store(res, var_target['ptr'])

    # --- JUMPS (Метки) ---
    elif 'set mento=' in line:
        name = line.split('=')[1].strip()
        bb = builder.append_basic_block(name)
        builder.branch(bb)
        builder.position_at_end(bb)
        mentos_blocks[name] = bb

    elif 'jump mentos=' in line:
        name = line.split('=')[1].strip()
        if name in mentos_blocks:
            builder.branch(mentos_blocks[name])
            next_bb = builder.append_basic_block(f"post_{name}")
            builder.position_at_end(next_bb)

# --- 7. СБОРКА И ЗАПУСК ---
def run_jit():

    main_builder.ret(ir.Constant(int64, 0)) # Завершаем main
    print("\n" + "═"*40)
    print(" XOOM 0.2.0 RUNNING (JIT)")
    print("═"*40)

    llvm_ir = str(module)
    mod = binding.parse_assembly(llvm_ir)
    tm = binding.Target.from_default_triple().create_target_machine()
    engine = binding.create_mcjit_compiler(mod, tm)
    engine.finalize_object()
    func_ptr = engine.get_function_address("main")

    cfunc = ctypes.CFUNCTYPE(ctypes.c_int64)(func_ptr)
    cfunc()
    print("\n" + "═"*40 + "\n DONE \n" + "═"*40)

def build_exe(name):
    main_builder.ret(ir.Constant(int64, 0))
    llvm_ir = str(module)
    mod = binding.parse_assembly(llvm_ir)
    tm = binding.Target.from_default_triple().create_target_machine()
    
    obj = tm.emit_object(mod)
    with open(f"{name}.o", "wb") as f: f.write(obj)
    
    os.system(f"clang {name}.o -o {name}.exe")
    print(f"Build complete: {name}.exe")
    try: os.remove(f"{name}.o")
    except: pass

# --- MAIN LOOP ---
if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python main.py run <file.xs>")
    else:
        cmd, path = sys.argv[1], sys.argv[2]
        
        # 1. Читаем основной файл
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # 2. ОБРАБОТКА INCLUDE (Собираем все библиотеки в один код)
        final_lines = []
        for line in lines:
            if line.strip().startswith("#include"):
                # Ищем название между < >
                m = re.search(r'<(.*?)>', line)
                if m:
                    lib_name = m.group(1)
                    lib_path = os.path.join("libraries", f"{lib_name}.xs")
                    if os.path.exists(lib_path):
                        with open(lib_path, 'r', encoding='utf-8') as lib_f:
                            final_lines.extend(lib_f.readlines())
                        print(f" [v0.2.1] Библиотека подключена: {lib_name}")
                    else:
                        print(f" [Ошибка] Файл библиотеки не найден: {lib_path}")
                continue # Не добавляем саму строку #include в компиляцию
            final_lines.append(line)
        
        # 3. КОМПИЛЯЦИЯ (Теперь проходим по всем собранным строкам)
        for line in final_lines:
            try:
                compile_line(line)
            except Exception as e:
                print(f" [Ошибка компиляции] В строке: {line.strip()}\n -> {e}")

        # 4. ЗАПУСК ИЛИ СБОРКА
        if cmd == 'run': 
            run_jit()
        elif cmd == 'build': 
            name = "output"
            for arg in sys.argv:
                if "name=" in arg: name = arg.split('=')[1]
            build_exe(name)
=======
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
>>>>>>> 9b88bb9c427a50e51508e61ad101476b383ae52b
