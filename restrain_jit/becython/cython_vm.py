from restrain_jit.becython.stack_vm_instructions import *
from restrain_jit.becython.phi_elim import main as phi_elim
from restrain_jit.becython.phi_node_analysis import main as phi_keep
from restrain_jit.becython.relabel import apply as relabel
from restrain_jit.becython.tools import show_instrs
from restrain_jit.jit_info import PyCodeInfo, PyFuncInfo
from restrain_jit.abs_compiler import instrnames as InstrNames
from restrain_jit.abs_compiler.from_bc import Interpreter
from restrain_jit.vm.am import AM, run_machine
from dataclasses import dataclass, field
from bytecode import Bytecode, ControlFlowGraph, Instr as PyInstr, CellVar, CompilerFlags
import typing as t
import types
import sys

Options = {
    'log-stack-vm': False,
    'log-phi': False,
    'phi-pass': "keep-phi",
}


def load_arg(x, cellvars, lineno):
    if x in cellvars:
        return PyInstr(InstrNames.LOAD_DEREF, CellVar(x), lineno=lineno)

    return PyInstr(InstrNames.LOAD_FAST, x, lineno=lineno)


def copy_func(f: types.FunctionType):
    # noinspection PyArgumentList
    nf = types.FunctionType(f.__code__, f.__globals__, None, None, f.__closure__)
    nf.__defaults__ = f.__defaults__
    nf.__name__ = f.__name__
    nf.__qualname__ = f.__qualname__
    nf.__module__ = f.__module__
    nf.__kwdefaults__ = f.__kwdefaults__
    nf.__annotations__ = f.__annotations__
    nf.__dict__ = f.__dict__
    return nf


@dataclass
class CyVM(AM[Instr, Repr]):
    _meta: dict

    # stack
    st: t.List[Repr]

    # instructions
    blocks: t.List[t.Tuple[t.Optional[str], t.List[A]]]

    # allocated temporary
    used: t.Set[str]
    unused: t.Set[str]
    globals: t.Set[str]
    module: types.ModuleType

    def set_lineno(self, lineno: int):
        self.add_instr(None, SetLineno(lineno))

    def get_module(self) -> types.ModuleType:
        return self.module

    def require_global(self, s: str):
        self.globals.add(s)

    @classmethod
    def func_info(cls, func: types.FunctionType):
        names = func.__code__.co_names
        code = Bytecode.from_code(func.__code__)
        codeinfo = cls.code_info(code)
        return PyFuncInfo(func.__name__, func.__module__, func.__defaults__,
                          func.__kwdefaults__, func.__closure__, func.__globals__, codeinfo,
                          func, {}, names)

    @classmethod
    def code_info(cls, code: Bytecode, *, debug_passes=()) -> PyCodeInfo[Repr]:

        cfg = ControlFlowGraph.from_bytecode(code)
        current = cls.empty()
        run_machine(Interpreter(code.first_lineno).abs_i_cfg(cfg), current)
        glob_deps = tuple(current.globals)
        instrs = current.instrs
        instrs = cls.pass_push_pop_inline(instrs)
        instrs = list(relabel(instrs))
        if Options.get('log-stack-vm'):
            print('DEBUG: stack-vm'.center(20, '='))
            show_instrs(instrs)

        phi_pass_name = Options['phi-pass']
        e = None
        try:
            phi_pass = {
                'phi-elim-by-move': phi_elim,
                'keep-phi': phi_keep
            }[Options['phi-pass']]
        except KeyError as ke:
            e = Exception("Unknown phi pass {!r}".format(phi_pass_name))
        if e is not None:
            raise e
        instrs = list(phi_pass(instrs))
        if Options.get('log-phi'):
            print('DEBUG: phi'.center(20, '='))
            show_instrs(instrs)
        return PyCodeInfo(code.name, tuple(glob_deps), code.argnames, code.freevars,
                          code.cellvars, code.filename, code.first_lineno, code.argcount,
                          code.kwonlyargcount, bool(code.flags & CompilerFlags.GENERATOR),
                          bool(code.flags & CompilerFlags.VARKEYWORDS),
                          bool(code.flags & CompilerFlags.VARARGS), instrs)

    def pop_exception(self, must: bool) -> Repr:
        raise NotImplemented

    def meta(self) -> dict:
        return self._meta

    def last_block_end(self) -> str:
        return self.end_label

    def push_block(self, end_label: str) -> None:
        self.blocks.append((end_label, []))

    def pop_block(self) -> Repr:
        # end_label, instrs = self.blocks.pop()
        # self.add_instr(None, PushUnwind())
        # for instr in instrs:
        #     self.add_instr(instr.lhs, instr.rhs)
        # self.add_instr(None, PopUnwind())
        # return Const(None)
        raise NotImplemented

    def from_const(self, val: Repr) -> object:
        assert isinstance(val, Const)
        return val.val

    def ret(self, val: Repr):
        return self.add_instr(None, Return(val))

    def const(self, val: object):
        return Const(val)

    @classmethod
    def reg_of(cls, n: str):
        return Reg(n)

    def from_higher(self, qualifier: str, name: str):
        assert not qualifier
        return Prim('', name)

    def from_lower(self, qualifier: str, name: str):
        return Prim(qualifier, name)

    def app(self, f: Repr, args: t.List[Repr]) -> Repr:
        name = self.alloc()
        reg = Reg(name)
        self.add_instr(name, App(f, args))
        return reg

    def store(self, n: str, val: Repr):
        self.add_instr(None, Store(Reg(n), val))

    def load(self, n: str) -> Repr:
        r = Reg(n)
        name = self.alloc()
        self.add_instr(name, Load(r))
        return Reg(name)

    def assign(self, n: str, v: Repr):
        self.add_instr(None, Ass(Reg(n), v))

    def peek(self, n: int):
        try:
            return self.st[-n - 1]
        except IndexError:
            name = self.alloc()
            self.add_instr(name, Peek(n))
            return name

    def jump(self, n: str):
        self.add_instr(None, Jmp(n))

    def jump_if_push(self, n: str, cond: Repr, leave: Repr):
        self.add_instr(None, JmpIfPush(n, cond, leave))

    def jump_if(self, n: str, cond: Repr):
        self.add_instr(None, JmpIf(n, cond))

    def label(self, n: str) -> None:
        self.st.clear()
        self.add_instr(None, Label(n))

    def push(self, r: Repr) -> None:
        self.st.append(r)
        self.add_instr(None, Push(r))

    def pop(self) -> Repr:
        try:

            a = self.st.pop()
            self.add_instr(None, Pop())
        except IndexError:
            name = self.alloc()
            self.add_instr(name, Pop())
            a = Reg(name)
        return a

    def release(self, name: Repr):
        """
        release temporary variable
        """
        if not isinstance(name, Reg):
            return
        name = name.n
        if name in self.used:
            self.used.remove(name)
            self.unused.add(name)

    def alloc(self):
        """
        allocate a new temporary variable
        """
        if self.unused:
            return self.unused.pop()
        tmp_name = f"tmp-{len(self.used)}"
        self.used.add(tmp_name)
        return tmp_name

    def add_instr(self, tag, instr: Instr):
        self.instrs.append(A(tag, instr))
        return None

    @property
    def instrs(self):
        return self.blocks[-1][1]

    @property
    def end_label(self) -> t.Optional[str]:
        return self.blocks[-1][0]

    @classmethod
    def empty(cls, module=None):
        return cls({}, [], [(None, [])], set(), set(), set(), module
                   or sys.modules[cls.__module__])

    @classmethod
    def pass_push_pop_inline(cls, instrs):
        blacklist = set()
        i = 0
        while True:
            try:
                assign = instrs[i]
                k, v = assign.lhs, assign.rhs
            except IndexError:
                break

            if k is None and isinstance(v, Pop):
                j = i - 1
                while True:
                    assign = instrs[j]
                    k, v = assign.lhs, assign.rhs
                    if k is None and isinstance(v, Push):
                        try:
                            assign = instrs[i]
                            k, v = assign.lhs, assign.rhs
                        except IndexError:
                            break

                        if k is None and isinstance(v, Pop):
                            pass
                        else:
                            break

                        blacklist.add(j)
                        blacklist.add(i)
                        i += 1
                        j -= 1

                        try:
                            assign = instrs[j]
                            k, v = assign.lhs, assign.rhs
                        except IndexError:
                            break
                        if k is None and isinstance(v, Push):
                            continue
                        break

                    else:
                        i += 1
                        break
            else:
                i = i + 1

        return [each for i, each in enumerate(instrs) if i not in blacklist]
