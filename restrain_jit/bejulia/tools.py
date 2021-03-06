from restrain_jit.bejulia.instructions import UnwindBlock, App, Const
from restrain_jit.jit_info import PyCodeInfo


def show_instrs(instrs, indent=''):

    for a in instrs:
        k = a.lhs
        v = a.rhs
        if k is not None:
            print(indent + k, '=', end=' ')
        else:
            print(indent, end='')
        next_indent = indent + '        '
        if isinstance(v, UnwindBlock):
            print()
            print(next_indent, 'Unwind', sep='')
            show_instrs(v.instrs, next_indent)
        elif isinstance(v, App):
            print('call', v.f)
            for each in v.args:
                if isinstance(each, Const) and isinstance(
                        each.val, PyCodeInfo):
                    print(next_indent, "function", each.val.name)
                    show_instrs(each.val.instrs,
                                next_indent + "         ")
                else:
                    print(next_indent, each)

        else:
            print(v)
